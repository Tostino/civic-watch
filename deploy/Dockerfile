# civic-watch, whole. The reader API and the reading surfaces in one image.
#
# Build from the REPOSITORY ROOT:
#     docker build -f deploy/Dockerfile -t civic-watch .
#
# One container rather than two, deliberately: the UI reaches the API over
# 127.0.0.1, so there is no inter-container DNS, no custom network, no Compose
# Manager — a plain Unraid template runs it. The API and the UI version together
# anyway; a route added to one is consumed by the other in the same commit.
#
# Layer order matters more than usual here. torch and the Python dependencies
# are ~1.2 GB and almost never change; they sit BELOW the application code, so a
# UI edit rebuilds and repushes only the layers above them. That is why bundling
# costs nothing per deploy — registries move changed layers, not whole images.

# --------------------------------------------------------------- ui build
# Debian-based on purpose, matching the runtime below: the standalone bundle can
# carry native artifacts, and building on alpine/musl to run on glibc is a trap
# that surfaces at runtime rather than at build.
FROM node:22-slim AS uibuild
WORKDIR /build
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

# ----------------------------------------------------------------- runtime
FROM python:3.12-slim AS runtime

# torch from PyTorch's CPU index. The PyPI wheel bundles CUDA — ~2.5 GB of it —
# for a container that will never see a GPU.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        torch==2.6.0
COPY deploy/requirements-api.txt /tmp/requirements-api.txt
RUN pip install --no-cache-dir -r /tmp/requirements-api.txt && rm /tmp/requirements-api.txt

# Just the node binary. Next's standalone output bundles its own node_modules,
# so npm and the rest of the Node distribution are dead weight at runtime.
COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node

WORKDIR /app

# The API: bin/ as well as web/, because server.py puts <root>/bin on sys.path —
# retrieve/ask/db live with the pipeline rather than with the server.
COPY bin/ /app/bin/
COPY web/ /app/web/

# The UI: standalone server, its static assets, and public/.
COPY --from=uibuild /build/.next/standalone/ /app/ui/
COPY --from=uibuild /build/.next/static/     /app/ui/.next/static/
COPY --from=uibuild /build/public/           /app/ui/public/

COPY deploy/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Where the 0.6B embedding model is cached. Mount a volume here, or it is
# re-downloaded on every container recreation — ~1.2 GB, and the first reader
# waits for it. The host directory must be writable by uid 65534.
ENV HF_HOME=/models
RUN mkdir -p /models && chown -R nobody:nogroup /models

# admin.init() writes a fresh token on every process start and /app is
# root-owned, so as `nobody` the server died on boot with PermissionError before
# serving a request. O_CREAT cannot make the file in a directory `nobody` may
# not write; O_WRONLY|O_TRUNC on a file it already owns is fine.
RUN touch /app/.admin_token && chown nobody:nogroup /app/.admin_token \
 && chmod 600 /app/.admin_token

# The query encoder runs on the CPU. Load-bearing: unset, retrieve.DEVICE falls
# back to cuda:1, the model fails to load, and tools.warm() degrades to
# BM25-only search. Startup says READY or UNAVAILABLE either way — grep
# "dense retrieval".
ENV PASCO_EMBED_DEVICE=cpu

# Next talks to the API over loopback inside this container. The API binds
# 127.0.0.1 only (see entrypoint.sh), so /api/admin is not on the network at
# all, and ui/proxy.ts 404s the console independently of any peer address or
# header. Three locks, none of which depend on the edge being configured right.
ENV ARCHIVE_API=http://127.0.0.1:8765
ENV ADMIN_DISABLED=1

ENV PYTHONUNBUFFERED=1 NODE_ENV=production PORT=3000

USER nobody
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:3000/api/bodies', timeout=4).status==200 else 1)"

ENTRYPOINT ["/app/entrypoint.sh"]
