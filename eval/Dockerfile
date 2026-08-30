FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Install opencode — ensure it's on PATH
RUN curl -fsSL https://opencode.ai/install | bash \
    && ln -sf /root/.opencode/bin/opencode /usr/local/bin/opencode \
    && opencode --version || (npm install -g opencode && ln -sf /usr/local/bin/opencode /usr/local/bin/opencode 2>/dev/null; true)

# SWE-bench harness
RUN pip install --no-cache-dir swebench datasets 2>&1 | tail -2

WORKDIR /darwin
COPY . .

RUN npm install 2>&1 | tail -2 || true
RUN npx tsc -p tsconfig.json --noEmit 2>&1 | tail -3 || true

ENV OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=1
ENV DARWIN_HOME=/darwin/workdir/darwin-data
ENV PATH="/root/.opencode/bin:/usr/local/bin:${PATH}"

# Default: smoke test (2 tasks). Override with --tasks 50 for full pilot.
CMD ["bash", "eval/scripts/run_matrix.sh", "--tasks", "2"]
