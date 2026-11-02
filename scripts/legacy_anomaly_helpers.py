"""Shared historical experiment calculations, not the current training policy.

Retain the original median imputation and in-sample IF behavior for reproduction.
The current temporal trainers must not use these compatibility helpers.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler


def prepare_feature_matrix(df: pd.DataFrame, features: list[str]) -> np.ndarray:
    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    return scaler.fit_transform(imputer.fit_transform(df[features]))


def run_legacy_if(X_scaled: np.ndarray, contamination: float) -> np.ndarray:
    model = IsolationForest(
        n_estimators=300,
        contamination=contamination,
        random_state=42,
        n_jobs=-1,
    )
    return (model.fit_predict(X_scaled) == -1).astype(int)


def jaccard(first: pd.Series, second: pd.Series) -> float:
    first_bool = first.astype(bool)
    second_bool = second.astype(bool)
    union = (first_bool | second_bool).sum()
    if union == 0:
        return np.nan
    return (first_bool & second_bool).sum() / union
