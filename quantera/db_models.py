"""Import every model so Base.metadata is complete (Alembic, tests)."""

from quantera.alerts.models import AlertEvent, AlertRule
from quantera.analysis.models import AnalysisJob, DecisionRecord
from quantera.collab.models import Invitation, ShareGrant
from quantera.construction.models import ProposalApproval
from quantera.core.audit import AuditEvent
from quantera.core.db import Base
from quantera.identity.models import (
    Membership,
    MfaRecoveryCode,
    PasswordReset,
    User,
    UserSession,
    Workspace,
)
from quantera.models_ml.models import MlModel, MlPrediction
from quantera.paper.models import PaperOrder, PaperOrderEvent, RiskBreach, RiskPolicy
from quantera.plans.models import TradePlan, TradePlanEvent
from quantera.portfolios.models import Portfolio, PortfolioTransaction
from quantera.universes.models import Universe, UniverseSnapshot, Watchlist, WatchlistItem

__all__ = [
    "AlertEvent",
    "AlertRule",
    "AnalysisJob",
    "AuditEvent",
    "Base",
    "DecisionRecord",
    "Invitation",
    "Membership",
    "MfaRecoveryCode",
    "MlModel",
    "MlPrediction",
    "PaperOrder",
    "PaperOrderEvent",
    "PasswordReset",
    "Portfolio",
    "PortfolioTransaction",
    "ProposalApproval",
    "RiskBreach",
    "RiskPolicy",
    "ShareGrant",
    "TradePlan",
    "TradePlanEvent",
    "Universe",
    "UniverseSnapshot",
    "User",
    "UserSession",
    "Watchlist",
    "WatchlistItem",
    "Workspace",
]
