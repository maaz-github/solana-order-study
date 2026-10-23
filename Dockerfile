# Solana validator transaction-ordering measurement study
#
# Bounty: Superteam Ukraine — "Inside the Block: How Solana Validators Order
# Transactions" (2,500 USDC pool, deadline 2026-11-05)
#
# Everything below is free to run except the final mainnet measurement, which
# costs ~0.0015 SOL in transaction fees.

FROM python:3.12-slim

# No build toolchain is needed: the study uses only the standard library plus
# `base58`, and the live signing path pulls in `solana`/`solders` at runtime.
RUN pip install --no-cache-dir \
      "solana>=0.34.0" \
      "solders>=0.21.0" \
      "base58>=2.1.1" \
      "pytest>=8.0.0"

WORKDIR /study

COPY pyproject.toml ./
COPY src ./src
COPY tests ./tests
RUN pip install --no-cache-dir -e . 2>/dev/null || true

# Keys are never baked into the image. They arrive as a volume or env var.
ENV SOLANA_ORDER_LIVE=0
ENV PYTHONUNBUFFERED=1

# Default entrypoint is the dry run — safe, free, no keys required.
ENTRYPOINT ["python", "-m", "src.cli"]
CMD ["--discover-only"]
