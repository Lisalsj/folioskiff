"""Alembic runtime environment."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from paper_reader.agents import models as agent_models
from paper_reader.assets import models as asset_models
from paper_reader.assistant import models as assistant_models
from paper_reader.database import Base
from paper_reader.discovery import models as discovery_models
from paper_reader.jobs import models as job_models
from paper_reader.reader import models as reader_models
from paper_reader.translations import models as translation_models
from paper_reader.updates import models as update_models

_ = (
    job_models.JobRecord,
    asset_models.ModelAssetRecord,
    asset_models.ActiveModelAssetRecord,
    asset_models.ModelAssetReferenceRecord,
    update_models.UpdateSettingsRecord,
    agent_models.AgentSettingsRecord,
    agent_models.PromptTemplateVersionRecord,
    agent_models.AnalysisReportRecord,
    agent_models.AnalysisReportFragmentRecord,
    agent_models.AnalysisFailureDraftRecord,
    assistant_models.AssistantSessionRecord,
    assistant_models.AssistantContextSnapshotRecord,
    assistant_models.AssistantTaskRecord,
    assistant_models.AssistantAgentRunRecord,
    assistant_models.AssistantMessageRecord,
    assistant_models.AssistantFragmentRecord,
    assistant_models.AssistantCheckpointRecord,
    translation_models.TranslationArtifactRecord,
    translation_models.TranslationSegmentRecord,
    translation_models.TranslationTermRecord,
    translation_models.TranslationExportRecord,
    discovery_models.WorkRecord,
    reader_models.LibraryEntryRecord,
    reader_models.DocumentPageTextRecord,
    reader_models.ReadingPositionRecord,
    reader_models.AnnotationRecord,
    reader_models.AnnotationMigrationBatchRecord,
    reader_models.AnnotationMigrationCandidateRecord,
    reader_models.AnnotationMigrationDecisionRecord,
)
config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
