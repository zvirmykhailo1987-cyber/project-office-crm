"""Реєстрація всіх таблиць у SQLModel.metadata."""
from ..authkit import AuthSession  # noqa: F401
from .ai import ENGINES, AiRun  # noqa: F401
from .ai import KINDS as AI_KINDS  # noqa: F401
from .base import BaseRow, SyncMeta, utcnow  # noqa: F401
from .chat import (  # noqa: F401
    KIMNATA_OFIS,
    ChatAttachment,
    ChatMessage,
    ChatRead,
    ChatRoom,
)
from .collab import (  # noqa: F401
    APPROVAL_STATUSES,
    COMMENT_KINDS,
    ENTITIES,
    NOTIF_KINDS,
    RESOLUTIONS,
    Approval,
    Comment,
    Notification,
    entity_label,
    entity_url,
)
from .counterparty import (  # noqa: F401
    CATEGORIES,
    DATA_STATUSES,
    PARTY_ROLES,
    Counterparty,
    Person,
)
from .datasource import DataSource, ImportConflict, ImportRun  # noqa: F401
from .docs import (  # noqa: F401
    CHANNELS,
    DEFAULT_SETTINGS,
    DOC_KINDS,
    KIND_PREZENTATSIYA,
    REPLY_STATUSES,
    WORKFLOW,
    WORKFLOW_ORDER,
    DocTemplate,
    Outreach,
    Setting,
)
from .deal import (  # noqa: F401
    ACTIVITY_TYPES,
    DEAL_STATUSES,
    Activity,
    Deal,
    Document,
)
from .pipeline import (  # noqa: F401
    AVTO_UMOVY,
    MODELI_BALU,
    REZULTAT_STATUS,
    REZULTATY,
    Pipeline,
    Program,
    Stage,
)
from .task import (  # noqa: F401
    PRIORITIES,
    STATUSES,
    Task,
)
from .task import SOURCES as TASK_SOURCES  # noqa: F401
from .site import (  # noqa: F401
    GEO_PRECISION_LABELS,
    HRUBA_TOCHNIST,
    CLUSTER_TYPE_LABELS,
    CLUSTER_STAGES,
    MEMBER_STATUS,
    SITE_TYPES,
    Cluster,
    ClusterMember,
    Score,
    ScoreComponent,
    Site,
)
from .people import (  # noqa: F401
    CAP_ADMIN,
    CAP_APPROVE,
    CAP_CRM,
    CAP_DATA_SERVICE,
    CAP_DELETE,
    CAP_EDIT_ANY_DOC,
    CAP_SEND,
    ROLE_CAPS,
    ROLE_HINTS,
    ROLES,
    AuditEntry,
    User,
)
