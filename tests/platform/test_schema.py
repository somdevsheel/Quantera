"""Guards: every model module is registered, and the migrations match the models exactly."""

from __future__ import annotations

import importlib
import pkgutil

from alembic import command
from alembic.config import Config

import quantera
from quantera.db_models import Base
from tests.platform.conftest import ROOT


def test_every_model_module_is_registered():
    registered = set(Base.metadata.tables)
    for mod in pkgutil.walk_packages(quantera.__path__, "quantera."):
        if mod.name.endswith(".models"):
            importlib.import_module(mod.name)
    assert set(Base.metadata.tables) == registered, (
        "a models module is missing from quantera/db_models.py: "
        f"{sorted(set(Base.metadata.tables) - registered)}")


def test_migrations_match_models(schema):
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["schema"] = schema
    command.check(cfg)  # raises if autogenerate would produce any operation
