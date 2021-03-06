#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
from pathlib import Path

from wildfires.utils import handle_array_job_args

try:
    # This will only work after the path modification carried out in the job script.
    from specific import (
        CACHE_DIR,
        SimpleCache,
        cross_val_cache,
        data_split_cache,
        get_shap_values,
    )
except ImportError:
    """Not running as an HPC job yet."""


# About 2 s / sample
# Expect ~ 1 hr per job -> 2000 samples in 2 hrs each (allowing for poor performance)


def func():
    # Used to re-compute specific failed jobs, `None` otherwise.
    indices = [
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        17,
        22,
        23,
        24,
        26,
        27,
        28,
        32,
        35,
        36,
        42,
        46,
        48,
        58,
        59,
        60,
        61,
        62,
        64,
        75,
        76,
        77,
        78,
        79,
        80,
        81,
        82,
        83,
        89,
        90,
        91,
        92,
        93,
        94,
        95,
        96,
        97,
        98,
        99,
        100,
        101,
        102,
        110,
        113,
        114,
        115,
        116,
        117,
        118,
        119,
        120,
        121,
        137,
        138,
        139,
        140,
        141,
        142,
        145,
        146,
        147,
        148,
        149,
        150,
        151,
        152,
        153,
        154,
        155,
        156,
        182,
        183,
        213,
        214,
        215,
        216,
        217,
        218,
        219,
        220,
        221,
        222,
        223,
        224,
        225,
        226,
        227,
        241,
        242,
        243,
        244,
        245,
        246,
        247,
        248,
        249,
        250,
        251,
        252,
        253,
        254,
        255,
        256,
        257,
        258,
        259,
        260,
        261,
        262,
        263,
        264,
        265,
        332,
        338,
        339,
        340,
        344,
        345,
        346,
        348,
        351,
        352,
        353,
        354,
        355,
        356,
        357,
        358,
        359,
        360,
        361,
        362,
        363,
        364,
        365,
        366,
        367,
        368,
        369,
        375,
        390,
        391,
        392,
        393,
        394,
        395,
        396,
        399,
        400,
        401,
        402,
        403,
        404,
        406,
        407,
        408,
        409,
        410,
        411,
        412,
        413,
        414,
        415,
        416,
        417,
        418,
        419,
        420,
        421,
        426,
        444,
        445,
        446,
        447,
        448,
        449,
        450,
        451,
        452,
        453,
        454,
        455,
        456,
        484,
        487,
        488,
        527,
        529,
        530,
        531,
        533,
        534,
        535,
        536,
        537,
        538,
        539,
        540,
        572,
        574,
        575,
        577,
        578,
        579,
        580,
        581,
        584,
        585,
        586,
        587,
        588,
        603,
        617,
        618,
        619,
        620,
        621,
        622,
        623,
        624,
        628,
        629,
        630,
        631,
        632,
        633,
        634,
        635,
        636,
        637,
        638,
        639,
        640,
        641,
        643,
        644,
        647,
        648,
        649,
        650,
        651,
        652,
        666,
        679,
        685,
        686,
        695,
        696,
        697,
        698,
        699,
        700,
        701,
        702,
        704,
        705,
        706,
        707,
        708,
        709,
        710,
        711,
        712,
        713,
        714,
        715,
        717,
        718,
        719,
        720,
        721,
        722,
        724,
        725,
        727,
        728,
        729,
        730,
        731,
        732,
        733,
        734,
        745,
        746,
        769,
        770,
        771,
        782,
        784,
        785,
        786,
        787,
        788,
        789,
        790,
        791,
        792,
        793,
        794,
        795,
        796,
        797,
        798,
        799,
        800,
        801,
        802,
        803,
        804,
        805,
        806,
        807,
        808,
        809,
        818,
        834,
        843,
        845,
        846,
        847,
        849,
        850,
        851,
        852,
        856,
        857,
        868,
        869,
        892,
        893,
        894,
        895,
        941,
        942,
        963,
        965,
        972,
        973,
        974,
        988,
        989,
    ]

    index = int(os.environ["PBS_ARRAY_INDEX"])

    if indices is not None:
        index = indices[index]

    print("Index:", index)

    X_train, X_test, y_train, y_test = data_split_cache.load()
    results, rf = cross_val_cache.load()

    job_samples = 2000

    tree_path_dependent_shap_cache = SimpleCache(
        f"tree_path_dependent_shap_{index}_{job_samples}",
        cache_dir=os.path.join(CACHE_DIR, "shap"),
    )

    @tree_path_dependent_shap_cache
    def get_interact_shap_values(model, X):
        return get_shap_values(model, X, interaction=False)

    get_interact_shap_values(
        rf, X_train[index * job_samples : (index + 1) * job_samples]
    )


if __name__ == "__main__":
    handle_array_job_args(
        Path(__file__).resolve(),
        func,
        ncpus=1,
        mem="5gb",
        walltime="08:00:00",
        max_index=349,
    )
