FROM apify/actor-python:3.13

# Install uv and add it to PATH (fast, reproducible installs)
ENV PATH="/root/.local/bin:$PATH"
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
 && uv --version

# Copy dependency files first so Docker caches the install layer
COPY pyproject.toml uv.lock ./

# Install the locked dependencies into the system Python with uv
RUN echo "Python version:" \
 && python --version \
 && echo "UV version:" \
 && uv --version \
 && echo "Installing dependencies from uv.lock:" \
 && uv export --no-hashes --format requirements-txt > /tmp/requirements.txt \
 && uv pip install --system -r /tmp/requirements.txt \
 && rm -f /tmp/requirements.txt \
 && echo "All installed Python packages:" \
 && uv pip list

# Copy the rest of the source (after deps for fast incremental builds)
COPY . ./

# Verify the source compiles
RUN python3 -m compileall -q src/

# Launch the actor
CMD ["python3", "-m", "src"]
