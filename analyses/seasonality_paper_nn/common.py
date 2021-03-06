# -*- coding: utf-8 -*-
import concurrent.futures
import importlib.util
import logging
import math
import os
import pickle
import re
import sys
import warnings
from collections import defaultdict
from datetime import datetime
from functools import partial, reduce, wraps
from itertools import combinations, product
from operator import mul
from pathlib import Path
from time import time

import cartopy.crs as ccrs
import cloudpickle
import dask.distributed
import eli5
import iris
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy
import seaborn as sns
import shap
from alepython.ale import _sci_format, ale_plot, first_order_ale_quant
from dask.distributed import Client
from dateutil.relativedelta import relativedelta
from hsluv import hsluv_to_rgb, rgb_to_hsluv
from joblib import Parallel, delayed, parallel_backend
from loguru import logger as loguru_logger
from matplotlib.colors import SymLogNorm, from_levels_and_colors
from matplotlib.patches import Rectangle
from pdpbox import pdp
from sklearn.base import clone
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from wildfires.analysis import (
    FigureSaver,
    MidpointNormalize,
    constrained_map_plot,
    corr_plot,
    cube_plotting,
    data_processing,
    map_model_output,
    print_dataset_times,
    vif,
)
from wildfires.dask_cx1 import (
    CachedResults,
    DaskRandomForestRegressor,
    common_worker_threads,
    dask_fit_combinations,
    dask_fit_loco,
    fit_dask_sub_est_grid_search_cv,
    fit_dask_sub_est_random_search_cv,
    get_client,
    get_parallel_backend,
)
from wildfires.data import (
    DATA_DIR,
    HYDE,
    VODCA,
    WWLLN,
    AvitabileThurnerAGB,
    Copernicus_SWI,
    Datasets,
    ERA5_DryDayPeriod,
    ERA5_Temperature,
    ESA_CCI_Landcover_PFT,
    GFEDv4,
    GlobFluo_SIF,
    MOD15A2H_LAI_fPAR,
    dataset_times,
    dummy_lat_lon_cube,
    get_memory,
    regrid,
)
from wildfires.joblib.cloudpickle_backend import register_backend as register_cl_backend
from wildfires.logging_config import enable_logging
from wildfires.qstat import get_ncpus
from wildfires.utils import (
    NoCachedDataError,
    SimpleCache,
    Time,
    ensure_datetime,
    get_masked_array,
    get_unmasked,
    replace_cube_coord,
    shorten_columns,
    shorten_features,
)

if "TQDMAUTO" in os.environ:
    from tqdm.auto import tqdm
else:
    from tqdm import tqdm

loguru_logger.enable("alepython")
loguru_logger.remove()
loguru_logger.add(sys.stderr, level="WARNING")

logger = logging.getLogger(__name__)
enable_logging("jupyter")

warnings.filterwarnings("ignore", ".*Collapsing a non-contiguous coordinate.*")
warnings.filterwarnings("ignore", ".*DEFAULT_SPHERICAL_EARTH_RADIUS.*")
warnings.filterwarnings("ignore", ".*guessing contiguous bounds.*")

warnings.filterwarnings(
    "ignore", 'Setting feature_perturbation = "tree_path_dependent".*'
)

normal_coast_linewidth = 0.3
mpl.rc("figure", figsize=(14, 6))
mpl.rc("font", size=9.0)

register_cl_backend()
PAPER_DIR = Path(__file__).resolve().parent
data_memory = get_memory(PAPER_DIR.name, backend="cloudpickle", verbose=2)

map_figure_saver_kwargs = {"dpi": 1200}

figure_saver = FigureSaver(directories=Path("~") / "tmp" / PAPER_DIR.name, debug=True)
map_figure_saver = figure_saver(**map_figure_saver_kwargs)

# 9 colors used to differentiate varying the lags throughout.
lags = [0, 1, 3, 6, 9, 12, 18, 24]
lag_colors = sns.color_palette("Set1", desat=0.85)
lag_color_dict = {lag: color for lag, color in zip(lags, lag_colors)}

experiments = ["all", "15_most_important", "no_temporal_shifts", "best_top_15"]
experiment_colors = sns.color_palette("Set2")
experiment_color_dict = {
    experiment: color for experiment, color in zip(experiments, experiment_colors)
}
experiment_name_dict = {
    "all": "all",
    "best_top_15": "best top 15",
    "15_most_important": "top 15",
    "no_temporal_shifts": "no lags",
    "fapar_only": "best top 15 (fAPAR)",
    "sif_only": "best top 15 (SIF)",
    "lai_only": "best top 15 (LAI)",
    "vod_only": "best top 15 (VOD)",
    "lagged_fapar_only": "lagged fAPAR only",
    "lagged_sif_only": "lagged SIF only",
    "lagged_lai_only": "lagged LAI only",
    "lagged_vod_only": "lagged VOD only",
}
experiment_color_dict.update(
    {
        experiment_name_dict[experiment]: experiment_color_dict[experiment]
        for experiment in experiments
    }
)

experiment_markers = ["<", "o", ">", "x"]
experiment_marker_dict = {
    experiment: marker for experiment, marker in zip(experiments, experiment_markers)
}
experiment_marker_dict.update(
    {
        experiment_name_dict[experiment]: experiment_marker_dict[experiment]
        for experiment in experiments
    }
)

# SHAP parameters.
shap_params = {"job_samples": 2000}  # Samples per job.
shap_interact_params = {
    "job_samples": 50,  # Samples per job.
    "max_index": 5999,  # Maximum job array index (inclusive).
}

# Specify common RF (training) params.
n_splits = 5

default_param_dict = {"random_state": 1, "bootstrap": True}

param_dict = {
    **default_param_dict,
    "ccp_alpha": 2e-9,
    "max_depth": 18,
    "max_features": "auto",
    "min_samples_leaf": 3,
    "min_samples_split": 2,
    "n_estimators": 500,
}


def common_get_model(cache_dir, X_train=None, y_train=None):
    cached = CachedResults(
        estimator_class=DaskRandomForestRegressor,
        n_splits=n_splits,
        cache_dir=cache_dir,
    )
    model = DaskRandomForestRegressor(**param_dict)
    model_key = tuple(sorted(model.get_params().items()))
    try:
        model = cached.get_estimator(model_key)
    except KeyError:
        with parallel_backend("dask"):
            model.fit(X_train, y_train)
        cached.store_estimator(model_key, model)
    model.n_jobs = get_ncpus()
    return model


def common_get_model_scores(rf, X_test, X_train, y_test, y_train):
    rf.n_jobs = get_ncpus()
    with parallel_backend("threading", n_jobs=get_ncpus()):
        y_pred = rf.predict(X_test)
        y_train_pred = rf.predict(X_train)
    return {
        "test_r2": r2_score(y_test, y_pred),
        "test_mse": mean_squared_error(y_test, y_pred),
        "train_r2": r2_score(y_train, y_train_pred),
        "train_mse": mean_squared_error(y_train, y_train_pred),
    }


# Data filling params.
n_months = 3

filled_variables = {"SWI(1)", "FAPAR", "LAI", "VOD Ku-band", "SIF"}
filled_variables.update(shorten_features(filled_variables))


def fill_name(name):
    return f"{name} {n_months}NN"


def get_filled_names(names):
    if isinstance(names, str):
        return get_filled_names((names,))
    filled = []
    for name in names:
        if any(var in name for var in filled_variables):
            filled.append(fill_name(name))
        else:
            filled.append(name)
    return filled


def repl_fill_name(name, sub=""):
    fill_ins = fill_name("")
    return name.replace(fill_ins, sub)


def repl_fill_names(names, sub=""):
    if isinstance(names, str):
        return repl_fill_names((names,), sub=sub)
    return [repl_fill_name(name, sub=sub) for name in names]


feature_categories = {
    "meteorology": get_filled_names(
        ["Dry Day Period", "SWI(1)", "Max Temp", "Diurnal Temp Range", "lightning"]
    ),
    "human": ["pftCrop", "popd"],
    "landcover": ["pftHerb", "ShrubAll", "TreeAll", "AGB Tree"],
    "vegetation": get_filled_names(["VOD Ku-band", "FAPAR", "LAI", "SIF"]),
}

feature_order = {}
no_fill_feature_order = {}
counter = 0
for category, entries in feature_categories.items():
    for entry in entries:
        feature_order[entry] = counter
        no_fill_feature_order[entry.strip(fill_name(""))] = counter
        counter += 1

# If BA is included, position it first.
no_fill_feature_order["GFED4 BA"] = -1

# Creating the Data Structures used for Fitting
@data_memory.cache
def get_data(
    shift_months=[1, 3, 6, 9, 12, 18, 24],
    selection_variables=None,
    masks=None,
    n_months=n_months,
):
    target_variable = "GFED4 BA"

    # Variables required for the above.
    required_variables = [target_variable]

    # Dataset selection.

    selection_datasets = [
        AvitabileThurnerAGB(),
        ERA5_Temperature(),
        ESA_CCI_Landcover_PFT(),
        GFEDv4(),
        HYDE(),
        WWLLN(),
    ]

    # Datasets subject to temporal interpolation (filling).
    temporal_interp_datasets = [
        Datasets(Copernicus_SWI()).select_variables(("SWI(1)",)).dataset
    ]

    # Datasets subject to temporal interpolation and shifting.
    shift_and_interp_datasets = [
        Datasets(MOD15A2H_LAI_fPAR()).select_variables(("FAPAR", "LAI")).dataset,
        Datasets(VODCA()).select_variables(("VOD Ku-band",)).dataset,
        Datasets(GlobFluo_SIF()).select_variables(("SIF",)).dataset,
    ]

    # Datasets subject to temporal shifting.
    datasets_to_shift = [
        Datasets(ERA5_DryDayPeriod()).select_variables(("Dry Day Period",)).dataset
    ]

    all_datasets = (
        selection_datasets
        + temporal_interp_datasets
        + shift_and_interp_datasets
        + datasets_to_shift
    )

    # Determine shared temporal extent of the data.
    min_time, max_time = dataset_times(all_datasets)[:2]
    shift_min_time = min_time - relativedelta(years=2)

    interp_min_time, interp_max_time = dataset_times(
        temporal_interp_datasets + shift_and_interp_datasets
    )[:2]
    target_timespan = (
        max(shift_min_time, interp_min_time + relativedelta(months=+n_months)),
        min(max_time, interp_max_time - relativedelta(months=+n_months)),
    )

    # Sanity check.
    assert min_time == datetime(2010, 1, 1)
    assert shift_min_time == datetime(2008, 1, 1)
    assert max_time == datetime(2015, 4, 1)

    # Carry out the temporal NN interpolation.
    for datasets in (temporal_interp_datasets, shift_and_interp_datasets):
        for i, dataset in enumerate(datasets):
            datasets[i] = dataset.get_temporally_interpolated_dataset(
                target_timespan, n_months
            )

    datasets_to_shift.extend(shift_and_interp_datasets)
    selection_datasets += datasets_to_shift
    selection_datasets += temporal_interp_datasets

    if shift_months is not None:
        for shift in shift_months:
            for shift_dataset in datasets_to_shift:
                selection_datasets.append(
                    shift_dataset.get_temporally_shifted_dataset(
                        months=-shift, deep=False
                    )
                )

    if selection_variables is None:
        selection_variables = get_filled_names(
            [
                "AGB Tree",
                "Diurnal Temp Range",
                "Dry Day Period",
                "FAPAR",
                "LAI",
                "Max Temp",
                "SIF",
                "SWI(1)",
                "ShrubAll",
                "TreeAll",
                "VOD Ku-band",
                "lightning",
                "pftCrop",
                "pftHerb",
                "popd",
            ]
        )
        if shift_months is not None:
            for shift in shift_months:
                selection_variables.extend(
                    [
                        f"{var} {-shift} Month"
                        for var in get_filled_names(
                            ["LAI", "FAPAR", "Dry Day Period", "VOD Ku-band", "SIF"]
                        )
                    ]
                )

    selection_variables = list(set(selection_variables).union(required_variables))

    selection = Datasets(selection_datasets).select_variables(selection_variables)

    # Ensure correct number of samples (in time).
    overall_min_time, overall_max_time = dataset_times(selection)[:2]
    for dataset in selection:
        dataset.limit_months(overall_min_time, overall_max_time)

        if dataset.frequency == "monthly":
            for cube in dataset:
                assert cube.shape[0] == 61

    (
        endog_data,
        exog_data,
        master_mask,
        filled_datasets,
        masked_datasets,
        land_mask,
    ) = data_processing(
        selection,
        which="climatology",
        transformations={},
        deletions=[],
        use_lat_mask=False,
        use_fire_mask=False,
        target_variable=target_variable,
        masks=masks,
    )
    return (
        endog_data,
        exog_data,
        master_mask,
        filled_datasets,
        masked_datasets,
        land_mask,
    )


@data_memory.cache
def get_offset_data(
    shift_months=[1, 3, 6, 9, 12, 18, 24],
    selection_variables=None,
    masks=None,
    n_months=n_months,
):
    (
        endog_data,
        exog_data,
        master_mask,
        filled_datasets,
        masked_datasets,
        land_mask,
    ) = get_data(
        shift_months=shift_months,
        selection_variables=selection_variables,
        masks=masks,
        n_months=n_months,
    )

    to_delete = []

    for column in exog_data:
        match = re.search(r"-\d{1,2}", column)
        if match:
            span = match.span()
            # Change the string to reflect the shift.
            original_offset = int(column[slice(*span)])
            if original_offset > -12:
                # Only shift months that are 12 or more months before the current month.
                continue
            comp = -(-original_offset % 12)
            new_column = " ".join(
                (
                    column[: span[0] - 1],
                    f"{original_offset} - {comp}",
                    column[span[1] + 1 :],
                )
            )
            if comp == 0:
                comp_column = column[: span[0] - 1]
            else:
                comp_column = " ".join(
                    (column[: span[0] - 1], f"{comp}", column[span[1] + 1 :])
                )
            print(column, comp_column)
            exog_data[new_column] = exog_data[column] - exog_data[comp_column]
            to_delete.append(column)

    for column in to_delete:
        del exog_data[column]

    return (
        endog_data,
        exog_data,
        master_mask,
        filled_datasets,
        masked_datasets,
        land_mask,
    )


def get_shap_values(rf, X, data=None, interaction=False):
    """Calculate SHAP values for `X`.

    When `data` is None, `feature_perturbation='tree_path_dependent'` by default.

    """
    if data is None:
        feature_perturbation = "tree_path_dependent"
    else:
        feature_perturbation = "interventional"

    explainer = shap.TreeExplainer(
        rf, data=data, feature_perturbation=feature_perturbation
    )

    if interaction:
        return explainer.shap_interaction_values(X)
    return explainer.shap_values(X)


def save_ale_2d_and_get_importance(
    model,
    train_set,
    features,
    n_jobs=8,
    include_first_order=False,
    figure_saver=None,
    plot_samples=True,
    figsize=None,
):
    model.n_jobs = n_jobs

    if figsize is None:
        if plot_samples:
            figsize = (10, 4.5)
        else:
            figsize = (7.5, 4.5)

    fig, ax = plt.subplots(
        1,
        2 if plot_samples else 1,
        figsize=figsize,
        gridspec_kw={"width_ratios": [1.7, 1]} if plot_samples else None,
        constrained_layout=True if plot_samples else False,
    )  # Make sure plot is plotted onto a new figure.
    with parallel_backend("threading", n_jobs=n_jobs):
        fig, axes, (quantiles_list, ale, samples) = ale_plot(
            model,
            train_set,
            features,
            bins=20,
            fig=fig,
            ax=ax[0] if plot_samples else ax,
            plot_quantiles=True,
            quantile_axis=True,
            plot_kwargs={
                "colorbar_kwargs": dict(
                    format="%.0e",
                    pad=0.02 if plot_samples else 0.09,
                    aspect=32,
                    shrink=0.85,
                    ax=ax[0] if plot_samples else ax,
                )
            },
            return_data=True,
            n_jobs=n_jobs,
            include_first_order=include_first_order,
        )

    # plt.subplots_adjust(top=0.89)
    for ax_key in ("ale", "quantiles_x"):
        axes[ax_key].xaxis.set_tick_params(rotation=45)

    if plot_samples:
        # Plotting samples.
        ax[1].set_title("Samples")
        # ax[1].set_xlabel(f"Feature '{features[0]}'")
        # ax[1].set_ylabel(f"Feature '{features[1]}'")
        mod_quantiles_list = []
        for axis, quantiles in zip(("x", "y"), quantiles_list):
            inds = np.arange(len(quantiles))
            mod_quantiles_list.append(inds)
            ax[1].set(**{f"{axis}ticks": inds})
            ax[1].set(**{f"{axis}ticklabels": _sci_format(quantiles, scilim=0.6)})
        samples_img = ax[1].pcolormesh(
            *mod_quantiles_list, samples.T, norm=SymLogNorm(linthresh=1)
        )
        fig.colorbar(samples_img, ax=ax, shrink=0.6, pad=0.01)
        ax[1].xaxis.set_tick_params(rotation=90)
        ax[1].set_aspect("equal")
        fig.set_constrained_layout_pads(
            w_pad=0.000, h_pad=0.000, hspace=0.0, wspace=0.015
        )

    if figure_saver is not None:
        figure_saver.save_figure(
            fig,
            "__".join(features),
            sub_directory="2d_ale_first_order" if include_first_order else "2d_ale",
        )

    #     min_samples = (
    #         train_set.shape[0] / reduce(mul, map(lambda x: len(x) - 1, quantiles_list))
    #     ) / 10
    #     return np.ma.max(ale[samples_grid > min_samples]) - np.ma.min(
    #         ale[samples_grid > min_samples]
    #     )

    return np.ptp(ale)


def save_pdp_plot_2d(model, X_train, features, n_jobs, figure_saver=None):
    model.n_jobs = n_jobs
    with parallel_backend("threading", n_jobs=n_jobs):
        pdp_interact_out = pdp.pdp_interact(
            model=model,
            dataset=X_train,
            model_features=X_train.columns,
            features=features,
            num_grid_points=[20, 20],
        )

    fig, axes = pdp.pdp_interact_plot(
        pdp_interact_out, features, x_quantile=True, figsize=(7, 8)
    )
    axes["pdp_inter_ax"].xaxis.set_tick_params(rotation=45)
    if figure_saver is not None:
        figure_saver.save_figure(fig, "__".join(features), sub_directory="pdp_2d")


def save_ale_plot_1d_with_ptp(
    model,
    X_train,
    column,
    n_jobs=8,
    monte_carlo_rep=1000,
    monte_carlo_ratio=100,
    verbose=False,
    monte_carlo=True,
    center=False,
    figure_saver=None,
):
    model.n_jobs = n_jobs
    with parallel_backend("threading", n_jobs=n_jobs):
        fig, ax = plt.subplots(
            figsize=(7.5, 4.5)
        )  # Make sure plot is plotted onto a new figure.
        out = ale_plot(
            model,
            X_train,
            column,
            bins=20,
            monte_carlo=monte_carlo,
            monte_carlo_rep=monte_carlo_rep,
            monte_carlo_ratio=monte_carlo_ratio,
            plot_quantiles=True,
            quantile_axis=True,
            rugplot_lim=0,
            scilim=0.6,
            return_data=True,
            return_mc_data=True,
            verbose=verbose,
            center=center,
        )
    if monte_carlo:
        fig, axes, data, mc_data = out
    else:
        fig, axes, data = out

    for ax_key in ("ale", "quantiles_x"):
        axes[ax_key].xaxis.set_tick_params(rotation=45)

    sub_dir = "ale" if monte_carlo else "ale_non_mc"
    if figure_saver is not None:
        figure_saver.save_figure(fig, column, sub_directory=sub_dir)

    if monte_carlo:
        mc_ales = np.array([])
        for mc_q, mc_ale in mc_data:
            mc_ales = np.append(mc_ales, mc_ale)
        return np.ptp(data[1]), np.ptp(mc_ales)
    else:
        return np.ptp(data[1])


def save_pdp_plot_1d(model, X_train, column, n_jobs, CACHE_DIR, figure_saver=None):
    data_file = os.path.join(CACHE_DIR, "pdp_data", column)

    if not os.path.isfile(data_file):
        model.n_jobs = n_jobs
        with parallel_backend("threading", n_jobs=n_jobs):
            pdp_isolate_out = pdp.pdp_isolate(
                model=model,
                dataset=X_train,
                model_features=X_train.columns,
                feature=column,
                num_grid_points=20,
            )
        os.makedirs(os.path.dirname(data_file), exist_ok=True)
        with open(data_file, "wb") as f:
            pickle.dump((column, pdp_isolate_out), f, -1)
    else:
        with open(data_file, "rb") as f:
            column, pdp_isolate_out = pickle.load(f)

    # With ICEs.
    fig_ice, axes_ice = pdp.pdp_plot(
        pdp_isolate_out,
        column,
        plot_lines=True,
        center=True,
        frac_to_plot=1000,
        x_quantile=True,
        figsize=(7, 5),
    )
    axes_ice["pdp_ax"].xaxis.set_tick_params(rotation=45)
    if figure_saver is not None:
        figure_saver.save_figure(fig_ice, column, sub_directory="pdp")

    # Without ICEs.
    fig_no_ice, ax = plt.subplots(figsize=(7.5, 4.5))
    plt.plot(pdp_isolate_out.pdp - pdp_isolate_out.pdp[0], marker="o")
    plt.xticks(
        ticks=range(len(pdp_isolate_out.pdp)),
        labels=_sci_format(pdp_isolate_out.feature_grids, scilim=0.6),
        rotation=45,
    )
    plt.xlabel(f"{column}")
    plt.title(f"PDP of feature '{column}'\nBins: {len(pdp_isolate_out.pdp)}")
    plt.grid(alpha=0.4, linestyle="--")
    if figure_saver is not None:
        figure_saver.save_figure(fig_no_ice, column, sub_directory="pdp_no_ice")
    return (fig_ice, fig_no_ice), pdp_isolate_out, data_file


def multi_ale_plot_1d(
    model,
    X_train,
    columns,
    fig_name,
    xlabel=None,
    ylabel=None,
    title=None,
    n_jobs=8,
    verbose=False,
    figure_saver=None,
):
    fig, ax = plt.subplots(
        figsize=(7.5, 4.5)
    )  # Make sure plot is plotted onto a new figure.
    model.n_jobs = n_jobs
    with parallel_backend("threading", n_jobs=n_jobs):
        quantile_list = []
        ale_list = []
        for feature in tqdm(
            columns, desc="Calculating feature ALEs", disable=not verbose
        ):
            quantiles, ale = first_order_ale_quant(
                model.predict, X_train, feature, bins=20
            )
            quantile_list.append(quantiles)
            ale_list.append(ale)

    # Construct quantiles from the individual quantiles, minimising the amount of interpolation.
    combined_quantiles = np.vstack([quantiles[None] for quantiles in quantile_list])

    final_quantiles = np.mean(combined_quantiles, axis=0)
    # Account for extrema.
    final_quantiles[0] = np.min(combined_quantiles)
    final_quantiles[-1] = np.max(combined_quantiles)

    mod_quantiles = np.arange(len(quantiles))
    for feature, quantiles, ale in zip(columns, quantile_list, ale_list):
        # Interpolate each of the quantiles relative to the accumulated final quantiles.
        ax.plot(
            np.interp(quantiles, final_quantiles, mod_quantiles),
            ale,
            marker="o",
            ms=3,
            label=feature,
        )

    ax.legend(loc="best")
    ax.set_xticks(mod_quantiles)
    ax.set_xticklabels(_sci_format(final_quantiles, scilim=0.6))
    ax.xaxis.set_tick_params(rotation=45)
    ax.grid(alpha=0.4, linestyle="--")

    fig.suptitle(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)

    if figure_saver is not None:
        figure_saver.save_figure(fig, fig_name, sub_directory="multi_ale")


def add_common_path_deco(f):
    """Add path to 'common' before executing a function remotely."""
    COMMON_DIR = Path(__file__).resolve().parent

    # Adding wraps(f) here causes issues with an unmodified path.
    def path_f(*args, **kwargs):
        if str(COMMON_DIR) not in sys.path:
            sys.path.insert(0, str(COMMON_DIR))
        # Call the original function.
        return f(*args, **kwargs)

    return path_f


def add_common_path(client):
    def _add_common():
        if str(PAPER_DIR) not in sys.path:
            sys.path.insert(0, str(PAPER_DIR))

    client.run(_add_common)


def get_lag(feature, target_feature=None):
    """Return the lag duration as an integer.

    Optionally a specific target feature can be required.

    Args:
        feature (str): Feature to extract month from.
        target_feature (str): If given, this feature is required for a successful
            match.

    Returns:
        int or None: For successful matches (see `target_feature`), an int
            representing the lag duration is returned. Otherwise, `None` is returned.

    """
    if target_feature is None:
        target_feature = ".*?"
    else:
        target_feature = re.escape(target_feature)

    # Avoid dealing with the fill naming.
    feature = feature.replace(fill_name(""), "")

    match = re.search(target_feature + r"\s-(\d+)\s", feature)
    if match is not None:
        return int(match.groups(default="0")[0])
    if match is None and re.match(target_feature, feature):
        return 0
    return None


def filter_by_month(features, target_feature, max_month):
    """Filter feature names using a single target feature and maximum month.

    Args:
        features (iterable of str): Feature names to select from.
        target_feature (str): String in `features` to match against.
        max_month (int): Maximum month.

    Returns:
        iterable of str: The filtered feature names, subset of `features`.

    """
    filtered = []
    for feature in features:
        lag = get_lag(feature, target_feature=target_feature)
        if lag is not None and lag <= max_month:
            filtered.append(feature)
    return filtered


def sort_features(features):
    """Sort feature names using their names and shift magnitudes.

    Args:
        features (iterable of str): Feature names to sort.

    Returns:
        list of str: Sorted list of features.

    """
    raw_features = []
    lags = []
    for feature in features:
        lag = get_lag(feature)
        assert lag is not None
        # Remove fill naming addition.
        feature = feature.replace(fill_name(""), "")
        if str(lag) in feature:
            # Strip lag information from the string.
            raw_features.append(feature[: feature.index(str(lag))].strip("-").strip())
        else:
            raw_features.append(feature)
        lags.append(lag)
    sort_tuples = tuple(zip(features, raw_features, lags))
    return [
        s[0]
        for s in sorted(
            sort_tuples, key=lambda x: (no_fill_feature_order[x[1]], abs(int(x[2])))
        )
    ]


def transform_series_sum_norm(x):
    x = x / np.sum(np.abs(x))
    return x


def plot_and_list_importances(importances, methods, print_n=15, N=15, verbose=True):
    fig, ax = plt.subplots()

    transformed = {}

    combined = None
    for method in methods:
        transformed[method] = transform_series_sum_norm(importances[method])
        if combined is None:
            combined = transformed[method].copy()
        else:
            combined += transformed[method]
    combined.sort_values(ascending=False, inplace=True)

    transformed = pd.DataFrame(transformed).reindex(combined.index, axis=0)

    for method, marker in zip(methods, ["o", "x", "s", "^"]):
        ax.plot(
            transformed[method], linestyle="", marker=marker, label=method, alpha=0.8
        )
    ax.set_xticklabels(
        transformed.index, rotation=45 if len(transformed.index) <= 15 else 90
    )
    ax.legend(loc="best")
    ax.grid(alpha=0.4)
    if verbose:
        print(combined[:print_n].to_latex())
    return combined[:N]


def calculate_2d_masked_shap_values(
    X_train, master_mask, valid_train_indices, shap_values
):
    masked_shap_arrs = []
    masked_shap_arrs_std = []
    vmins = []
    vmaxs = []
    std_vmins = []
    std_vmaxs = []

    for i, feature in enumerate(tqdm(X_train.columns, desc="Aggregating SHAP values")):
        masked_shap_comp = np.ma.MaskedArray(
            np.zeros_like(master_mask, dtype=np.float64), mask=np.ones_like(master_mask)
        )
        masked_shap_comp.ravel()[
            valid_train_indices[: shap_values.shape[0]]
        ] = shap_values[:, i]
        avg_shap_comp = np.ma.mean(masked_shap_comp, axis=0)
        masked_shap_arrs.append(avg_shap_comp)
        vmins.append(np.min(avg_shap_comp))
        vmaxs.append(np.max(avg_shap_comp))

        avg_shap_std = np.ma.std(masked_shap_comp, axis=0)
        masked_shap_arrs_std.append(avg_shap_std)
        std_vmins.append(np.min(avg_shap_std))
        std_vmaxs.append(np.max(avg_shap_std))

    vmin = min(vmins)
    vmax = max(vmaxs)

    std_vmin = min(std_vmins)
    std_vmax = max(std_vmaxs)

    return masked_shap_arrs, masked_shap_arrs_std, vmin, vmax, std_vmin, std_vmax


def plot_shap_value_maps(
    X_train,
    masked_shap_arrs,
    masked_shap_arrs_std,
    vmin,
    vmax,
    std_vmin,
    std_vmax,
    map_figure_saver,
):
    for i, feature in enumerate(tqdm(X_train.columns, desc="Mapping SHAP values")):
        fig = cube_plotting(
            masked_shap_arrs[i],
            fig=plt.figure(figsize=(5.1, 2.8)),
            title=f"Mean SHAP value for '{shorten_features(feature)}'",
            cmap="Spectral_r",
            nbins=7,
            cmap_midpoint=0,
            cmap_symmetric=True,
            vmin=vmin,
            vmax=vmax,
            log=True,
            log_auto_bins=False,
            min_edge=1e-3,
            extend="neither",
            colorbar_kwargs={
                "format": "%0.1e",
                "label": f"SHAP ('{shorten_features(feature)}')",
            },
            coastline_kwargs={"linewidth": 0.3},
        )
        map_figure_saver.save_figure(
            fig, f"shap_value_map_{feature}", sub_directory="shap_map"
        )
        fig = cube_plotting(
            masked_shap_arrs_std[i],
            fig=plt.figure(figsize=(5.1, 2.8)),
            title=f"STD SHAP value for '{shorten_features(feature)}'",
            cmap="YlOrRd",
            nbins=7,
            vmin=std_vmin,
            vmax=std_vmax,
            log=True,
            log_auto_bins=False,
            min_edge=1e-3,
            extend="neither",
            colorbar_kwargs={
                "format": "%0.1e",
                "label": f"SHAP ('{shorten_features(feature)}')",
            },
            coastline_kwargs={"linewidth": 0.3},
        )
        map_figure_saver.save_figure(
            fig, f"shap_value_std_map_{feature}", sub_directory="shap_map_std"
        )


def load_experiment_data(folders, which="all", ignore=()):
    """Load data from specified experiments.

    Args:
        folders (iterable of {str, Path}): Folder names corresponding to the
            experiments to load data for.
        which (iterable of {'all', 'offset_data', 'model', 'data_split', 'model_scores'}):
            'all' loads everything.
        ignore (iterable of str): Subsets of the above the ignore.

    Returns:
        dict of dict: Keys are the given `folders` and the loaded data types.

    """
    data = defaultdict(dict)
    if which == "all":
        which = ("offset_data", "model", "data_split", "model_scores")

    for experiment in folders:
        # Load the experiment module.
        spec = importlib.util.spec_from_file_location(
            f"{experiment}_specific",
            str(PAPER_DIR / experiment / "specific.py"),
        )
        module = importlib.util.module_from_spec(spec)
        data[experiment]["module"] = module
        # Load module contents.
        spec.loader.exec_module(module)

        if "offset_data" in which:
            data[experiment].update(
                {
                    key: data
                    for key, data in zip(
                        (
                            "endog_data",
                            "exog_data",
                            "master_mask",
                            "filled_datasets",
                            "masked_datasets",
                            "land_mask",
                        ),
                        module.get_offset_data(),
                    )
                    if key not in ignore
                }
            )
        if "model" in which:
            data[experiment]["model"] = module.get_model()
        if "data_split" in which:
            data[experiment].update(
                {
                    key: data
                    for key, data in zip(
                        (
                            key
                            for key in ("X_train", "X_test", "y_train", "y_test")
                            if key not in ignore
                        ),
                        module.data_split_cache.load(),
                    )
                }
            )
        if "model_scores" in which:
            data[experiment].update(module.get_model_scores())

    return data
