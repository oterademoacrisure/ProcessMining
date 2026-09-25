# POINT 19: quick smoke test for the pluggable vector layer (Step 1).
# Uses the FAISS backend + the embedder's offline fallback (no Azure needed).
import tempfile, os
from app.vectorstore import get_vector_store, Embedder, VectorRecord

emb = Embedder({"embed_dim": 64})
print("embedder mode:", emb.mode)

tmp = os.path.join(tempfile.gettempdir(), "smoke_precedent.faiss")
store = get_vector_store({"vector_backend": "faiss", "vector_index_path": tmp})
store.clear()

docs = {
    42: "appian webapp database connection pool exhausted timeout",
    87: "mule flow credit bureau api returning http 503 errors",
    13: "prometheus high cpu usage on payment service",
}
store.upsert([
    VectorRecord(id=i, vector=emb.embed_one(t), metadata={"tenant_id": 1})
    for i, t in docs.items()
])
print("indexed count:", store.count())

query = "db connection pool ran out of connections on appian"
hits = store.search(emb.embed_one(query), k=3, tenant_id=1)
print("query:", query)
for h in hits:
    print(f"  id={h.id}  score={h.score:.3f}  -> {docs[h.id]}")

# tenant scoping: nothing for a different tenant
print("other-tenant hits:", len(store.search(emb.embed_one(query), k=3, tenant_id=999)))
store.clear()
print("OK")
