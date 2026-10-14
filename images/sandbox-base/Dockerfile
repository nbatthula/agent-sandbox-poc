# Base sandbox image for AI agent execution
# Built for arm64 (Apple Silicon) - change to amd64 for x86
FROM ubuntu:24.04

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install base toolchain
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    wget \
    ca-certificates \
    python3 \
    python3-pip \
    python3-venv \
    nodejs \
    npm \
    build-essential \
    sudo \
    vim \
    htop \
    jq \
    unzip \
    rsync \
    openssh-client \
    chromium-browser \
    chromium-chromedriver \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user (UID 1001 to avoid conflict with ubuntu's default 1000)
ARG USERNAME=agent
ARG USER_UID=1001
ARG USER_GID=1001
RUN groupadd --gid $USER_GID $USERNAME \
    && useradd --uid $USER_UID --gid $USER_GID -m -s /bin/bash $USERNAME \
    && echo "$USERNAME ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/$USERNAME \
    && chmod 0440 /etc/sudoers.d/$USERNAME

# Set up workspace directory
RUN mkdir -p /workspace && chown -R $USERNAME:$USERNAME /workspace

# Install Python agent frameworks
RUN pip3 install --no-cache-dir --break-system-packages \
    langchain \
    langchain-openai \
    langchain-anthropic \
    langgraph \
    deepagents \
    pydantic \
    httpx \
    python-dotenv

# Install Node-based agent CLIs (optional, for Codex, etc.)
# RUN npm install -g @anthropic-ai/claude-code @openai/codex 2>/dev/null || true

# Switch to non-root user
USER $USERNAME
WORKDIR /workspace

# Environment
ENV HOME=/home/$USERNAME \
    PATH="/home/$USERNAME/.local/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/home/$USERNAME/.cache/ms-playwright

# Install Playwright for browser automation
RUN playwright install chromium 2>/dev/null || true

# Default command keeps container running for exec
CMD ["sleep", "infinity"]