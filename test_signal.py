_VECTOR_ONLY_FIELDS = frozenset({"embedding", "vector_status", "updated_at"})

def handle_product_updated(update_fields=None):
    if update_fields is not None and update_fields.issubset(_VECTOR_ONLY_FIELDS):
        print("Skipped")
        return
    print("Enqueued")

handle_product_updated(frozenset(["embedding", "vector_status", "updated_at"]))
handle_product_updated(None)
