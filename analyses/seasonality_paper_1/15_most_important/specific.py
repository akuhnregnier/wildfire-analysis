# -*- coding: utf-8 -*-
import sys
import warnings
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
if sys.path[0] != str(PROJECT_DIR.parent):
    sys.path.insert(0, str(PROJECT_DIR.parent))

warnings.filterwarnings(
    "ignore", category=FutureWarning, module="sklearn.utils.deprecation"
)
from common import *

warnings.filterwarnings(
    "always", category=FutureWarning, module="sklearn.utils.deprecation"
)

figure_saver = FigureSaver(
    directories=Path("~") / "tmp" / PROJECT_DIR.parent.name / PROJECT_DIR.name,
    debug=True,
)
map_figure_saver = figure_saver(**map_figure_saver_kwargs)

memory = get_memory("__".join((PROJECT_DIR.parent.name, PROJECT_DIR.name)), verbose=100)
CACHE_DIR = Path(DATA_DIR) / ".pickle" / PROJECT_DIR.parent.name / PROJECT_DIR.name

data_split_cache = SimpleCache("data_split", cache_dir=CACHE_DIR)
cross_val_cache = SimpleCache("rf_cross_val", cache_dir=CACHE_DIR)

save_ale_2d_and_get_importance = partial(
    save_ale_2d_and_get_importance, figure_saver=figure_saver
)
save_pdp_plot_2d = partial(save_pdp_plot_2d, figure_saver=figure_saver)
save_ale_plot_1d_with_ptp = partial(
    save_ale_plot_1d_with_ptp, figure_saver=figure_saver
)
save_pdp_plot_1d = partial(
    save_pdp_plot_1d, CACHE_DIR=CACHE_DIR, figure_saver=figure_saver
)
multi_ale_plot_1d = partial(multi_ale_plot_1d, figure_saver=figure_saver)

# Redefine the common functionality for our use-case - no shifted variables.
_common_get_data = get_data
_common_get_offset_data = get_offset_data


selected_features = (
    "Dry Day Period",
    "Max Temp",
    "VOD Ku-band -1 Month",
    "VOD Ku-band -3 Month",
    "FAPAR",
    "Dry Day Period -3 Month",
    "SIF",
    "LAI -3 Month",
    "VOD Ku-band",
    "VOD Ku-band -6 Month",
    "pftHerb",
    "popd",
    "lightning",
    "SIF -6 Month",
    "pftCrop",
)


@wraps(_common_get_data)
def get_data(*args, **kwargs):
    (
        endog_data,
        exog_data,
        master_mask,
        filled_datasets,
        masked_datasets,
        land_mask,
    ) = _common_get_data(*args, **kwargs)

    # We need to subset exog_data, filled_datasets, and masked_datasets.
    exog_data = exog_data[list(selected_features)]
    filled_datasets = filled_datasets.select_variables(selected_features)
    masked_datasets = masked_datasets.select_variables(selected_features)

    return (
        endog_data,
        exog_data,
        master_mask,
        filled_datasets,
        masked_datasets,
        land_mask,
    )


@wraps(_common_get_offset_data)
def get_offset_data(*args, **kwargs):
    (
        endog_data,
        exog_data,
        master_mask,
        filled_datasets,
        masked_datasets,
        land_mask,
    ) = _common_get_offset_data(*args, **kwargs)

    # We need to subset exog_data, filled_datasets, and masked_datasets.
    exog_data = exog_data[list(selected_features)]
    filled_datasets = filled_datasets.select_variables(selected_features)
    masked_datasets = masked_datasets.select_variables(selected_features)

    return (
        endog_data,
        exog_data,
        master_mask,
        filled_datasets,
        masked_datasets,
        land_mask,
    )
