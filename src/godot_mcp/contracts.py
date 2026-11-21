"""Declared results of the Godot tools."""

from typing import Any, Literal

from cavi_mcp_core.contracts import Result

View = Literal["front", "three-quarter", "side"]


class Status(Result):
    """Whether each review tool is set or installed."""

    CAVI_GODOT_BIN: bool
    CAVI_GODOT_WEB_TEMPLATE: bool
    CAVI_CHROMIUM_BIN: bool
    CAVI_NODE_BIN: bool


class ViewCapture(Result):
    image: str
    metadata: str


class Review(Result):
    """A review receipt: prepared, or captured with its bundle and evidence hashes."""

    version: Literal[1]
    kind: Literal["godot-asset-review"]
    source: str
    source_sha256: str
    view: View
    lighting: Literal["studio", "clay", "sun"] | None = None
    state: Literal["prepared", "captured"]
    camera_frame_evidence: Literal[True] | None = None
    all_views: Literal[True] | None = None
    pose: dict[str, Any] | None = None
    framing: dict[str, Any] | None = None
    project: dict[str, str] | None = None
    inputs: dict[str, Any] | None = None
    bundle: str | None = None
    godot_version: str | None = None
    renderer: str | None = None
    scene: dict[str, Any] | None = None
    artifacts: dict[str, str] | None = None
    view_captures: dict[View, ViewCapture] | None = None


class ReviewStatus(Review):
    """A review receipt with its pending cleanup and latest capture attempt."""

    cleanup_pending: bool
    latest_attempt: str | None


class EngineInputs(Result):
    source_sha256: str
    engine_sha256: str
    provider: dict[str, Any]


class BudgetCheck(Result):
    rule: str
    limit: int | float
    value: int | float | None
    within: bool


class BudgetResult(Result):
    """The requested limits, each limit's measured value, and whether all are met."""

    limits: dict[str, int | float]
    checks: list[BudgetCheck]
    within: bool


class AssetInspection(Result):
    """A source-bound Godot scene report and the hash of its saved copy."""

    kind: Literal["godot-asset-inspection"]
    source: str
    inputs: EngineInputs
    report_sha256: str
    report: dict[str, Any]
    budget: BudgetResult | None = None


class MotionInspection(Result):
    """Engine-evaluated node and bone transforms of one clip at the requested times."""

    kind: Literal["godot-motion-inspection"]
    source: str
    config: dict[str, Any]
    inputs: EngineInputs
    report_sha256: str
    report: dict[str, Any]
