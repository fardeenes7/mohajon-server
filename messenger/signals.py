from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from chat.models import FAQEntry
from chat.tasks.rag import embed_faq_entry

@receiver(post_save, sender=FAQEntry)
def trigger_faq_rag_indexing(sender, instance, **kwargs):
    """
    EPIC A-03: Enqueue an async task to generate semantic embeddings for RAG.
    """
    faq_entry_id = str(instance.pk)

    def _enqueue():
        embed_faq_entry.delay(faq_entry_id=faq_entry_id)

    transaction.on_commit(_enqueue)
