FROM python:3.12-slim

# Install Node.js 22 and system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    git \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Clone claude-code-telegram
RUN git clone https://github.com/ECAllen/claude-code-telegram.git /app
WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install telegram-sender skill globally so Claude can send messages back to you
RUN mkdir -p /root/.claude/skills && \
    cp -r /app/skills/telegram-sender /root/.claude/skills/

# Create workspace directory (mount your project/CLAUDE.md here)
RUN mkdir -p /workspace

ENV CLAUDE_WORKSPACE=/workspace
ENV CLAUDE_PATH=claude
ENV PYTHONUNBUFFERED=1

CMD ["python", "/app/telegram-bot.py"]
