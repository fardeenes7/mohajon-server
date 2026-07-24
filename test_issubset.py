_VECTOR_ONLY_FIELDS = frozenset({"embedding", "vector_status", "updated_at"})
update_fields = ["embedding", "vector_status", "updated_at"]

_update_fields = frozenset(update_fields) if update_fields else None

print("issubset:", _update_fields.issubset(_VECTOR_ONLY_FIELDS))
