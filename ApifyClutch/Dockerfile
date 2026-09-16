# Builder stage: the only place the GitHub token exists.
FROM apify/actor-python:3.13 AS builder

ENV PATH="/root/.local/bin:$PATH"
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
 && uv --version

# Apify passes GH_EMAIL_ENRICH_TOKEN as a build arg when "apply to build" is on.
# It must not be a secret env var, or this stage cannot see it.
ARG GH_EMAIL_ENRICH_TOKEN
RUN apt-get update && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/* \
 && git config --global \
    url."https://x-access-token:${GH_EMAIL_ENRICH_TOKEN}@github.com/".insteadOf \
    "https://github.com/"

COPY pyproject.toml uv.lock ./
RUN echo "Python version:" \
 && python --version \
 && echo "Installing dependencies from uv.lock:" \
 && uv export --no-hashes --format requirements-txt > /tmp/requirements.txt \
 && uv pip install --system -r /tmp/requirements.txt \
 && rm -f /tmp/requirements.txt

# Runtime stage: builder gitconfig and token do not ship.
FROM apify/actor-python:3.13

ENV PATH="/root/.local/bin:$PATH"
ENV GH_EMAIL_ENRICH_TOKEN=""
COPY --from=builder /usr/local /usr/local
COPY . ./
RUN python3 -m compileall -q src/
CMD ["python3", "-m", "src"]
