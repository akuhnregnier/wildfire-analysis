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


def func():
    # Used to re-compute specific failed jobs, `None` otherwise.
    indices = [
        32,
        71,
        277,
        278,
        279,
        280,
        281,
        282,
        283,
        284,
        339,
        340,
        341,
        342,
        343,
        344,
        379,
        481,
        483,
        498,
        500,
        515,
        516,
        517,
        518,
        519,
        520,
        521,
        522,
        523,
        524,
        564,
        565,
        566,
        589,
        903,
        904,
        905,
        906,
        907,
        908,
        914,
        915,
        994,
        995,
        1016,
        1023,
        1024,
        1025,
        1026,
        1085,
        1103,
        1107,
        1123,
        1126,
        1127,
        1128,
        1243,
        1271,
        1272,
        1324,
        1378,
        1385,
        1386,
        1387,
        1388,
        1389,
        1390,
        1391,
        1403,
        1404,
        1416,
        1418,
        1438,
        1440,
        1455,
        1456,
        1457,
        1589,
        1592,
        1626,
        1627,
        1629,
        1675,
        1676,
        2008,
        2009,
        2010,
        2153,
        2160,
        2164,
        2167,
        2169,
        2205,
        2208,
        2248,
        2249,
        2250,
        2277,
        2297,
        2298,
        2299,
        2328,
        2337,
        2342,
        2343,
        2379,
        2381,
        2382,
        2383,
        2604,
        2919,
        2920,
        3036,
        3048,
        3049,
        3050,
        3051,
        3052,
        3059,
        3065,
        3066,
        3067,
        3068,
        3075,
        3076,
        3077,
        3078,
        3079,
        3080,
        3081,
        3082,
        3083,
        3085,
        3091,
        3092,
        3093,
        3094,
        3095,
        3102,
        3103,
        3104,
        3105,
        3106,
        3107,
        3108,
        3109,
        3110,
        3111,
        3112,
        3113,
        3114,
        3115,
        3124,
        3126,
        3170,
        3171,
        3299,
        3301,
        3307,
        3343,
        3716,
        3733,
        3734,
        3735,
        3736,
        3751,
        3753,
        3757,
        3817,
        3818,
        3828,
        4046,
        4047,
        4048,
        4049,
        4050,
        4051,
        4052,
        4053,
        4054,
        4055,
        4056,
        4057,
        4058,
        4059,
        4060,
        4061,
        4062,
        4063,
        4064,
        4065,
        4066,
        4067,
        4074,
        4075,
        4080,
        4081,
        4082,
        4083,
        4084,
        4085,
        4086,
        4087,
        4088,
        4089,
        4090,
        4091,
        4092,
        4093,
        4094,
        4095,
        4096,
        4097,
        4098,
        4099,
        4100,
        4101,
        4102,
        4336,
        4346,
        4347,
        4384,
        4385,
        4386,
        4387,
        4388,
        4389,
        4404,
        4420,
        4422,
        4475,
        4483,
        4508,
        4512,
        4527,
        4528,
        4607,
        4608,
        4609,
        4610,
        4611,
        4612,
        4613,
        4614,
        4615,
        4616,
        4694,
        4695,
        4696,
        4697,
        4729,
        4730,
        4740,
        4761,
        4837,
        4838,
        4880,
        4881,
        4882,
        4991,
        4992,
        5032,
        5061,
        5087,
        5096,
        5186,
        5187,
        5188,
        5189,
        5190,
        5191,
        5192,
        5193,
        5199,
        5407,
        5408,
        5451,
        5452,
        5511,
        5598,
        5599,
        5600,
        5601,
        5602,
        5603,
        5604,
        5605,
        5606,
        5607,
        5608,
        5620,
        5662,
        5667,
        5699,
        5700,
        5757,
        5763,
        5852,
        5853,
        5854,
        5855,
        5856,
        5857,
        5941,
        5991,
    ]

    index = int(os.environ["PBS_ARRAY_INDEX"])

    if indices is not None:
        index = indices[index]

    print("Index:", index)

    X_train, X_test, y_train, y_test = data_split_cache.load()
    results, rf = cross_val_cache.load()

    job_samples = 50

    tree_path_dependent_shap_interact_cache = SimpleCache(
        f"tree_path_dependent_shap_interact_{index}_{job_samples}",
        cache_dir=os.path.join(CACHE_DIR, "shap_interaction"),
    )

    @tree_path_dependent_shap_interact_cache
    def get_interact_shap_values(model, X):
        return get_shap_values(model, X, interaction=True)

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
        max_index=305,
    )
