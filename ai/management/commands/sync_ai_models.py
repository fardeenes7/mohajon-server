from __future__ import annotations

from django.core.management.base import BaseCommand

from ai.services.model_sync import sync_ai_models


class Command(BaseCommand):
    help = (
        "Sync Google + OpenAI text and embedding models from the Vercel AI "
        "gateway into the AIModelRegistry. Marks each model's capability "
        "(text or embedding) and, on first run, seeds the handcoded default "
        "and fallback models (configurable afterward from the Django admin)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--no-defaults",
            action="store_true",
            help="Only upsert models; do not seed the handcoded default/fallback ladder.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and classify models, report what would change, but roll back.",
        )

    def handle(self, *args, **options) -> None:
        apply_defaults = not options["no_defaults"]
        dry_run = options["dry_run"]

        if dry_run:
            # Run inside a transaction we deliberately roll back so nothing
            # persists while still exercising the real upsert path.
            from django.db import transaction

            self.stdout.write(self.style.WARNING("Dry run — no changes will be saved."))
            with transaction.atomic():
                stats = sync_ai_models(apply_defaults=apply_defaults)
                transaction.set_rollback(True)
        else:
            stats = sync_ai_models(apply_defaults=apply_defaults)

        self.stdout.write(
            self.style.SUCCESS(
                f"Synced models: {stats.created} created, {stats.updated} updated, "
                f"{stats.skipped} skipped (non-text/embedding or other providers)."
            )
        )
        for line in stats.defaults_set:
            self.stdout.write(self.style.SUCCESS(f"  default set: {line}"))
        for err in stats.errors:
            self.stdout.write(self.style.ERROR(f"  error: {err}"))
