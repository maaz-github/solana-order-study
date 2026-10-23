# Makefile — the one-command entry points the bounty asks for.
#
# `docker compose` is not available on every host (the plugin is missing on
# some Docker installs), so these targets shell out to `docker build` +
# `docker run` directly. Same image, same volumes, same guarantees.

IMAGE ?= solana-order-study

.PHONY: help build test dry-run live clean

help:
	@echo "make build     - build the image"
	@echo "make test      - unit tests (no network, no keys, free)"
	@echo "make dry-run   - validator discovery + analysis (no keys, no fees)"
	@echo "make live      - real mainnet measurement (needs SOLANA_KEYPAIR_PATH)"
	@echo "make clean     - remove the image"

build:
	docker build -t $(IMAGE) .

test:
	docker run --rm $(IMAGE) -m pytest tests/ -q

dry-run:
	docker run --rm \
	  -e SOLANA_ORDER_LIVE=0 \
	  -e SOLANA_RPC_URL=$(SOLANA_RPC_URL) \
	  -v $(PWD)/data:/study/data \
	  $(IMAGE) --discover-only

live:
	@test -n "$(SOLANA_KEYPAIR_PATH)" || { echo "SOLANA_KEYPAIR_PATH is required"; exit 1; }
	docker run --rm \
	  -e SOLANA_ORDER_LIVE=1 \
	  -e SOLANA_KEYPAIR_PATH=/keys/funder.json \
	  -e SOLANA_RPC_URL=$(SOLANA_RPC_URL) \
	  -e SOLANA_ORDER_SPEND_CAP=$(SOLANA_ORDER_SPEND_CAP) \
	  -v $(PWD)/data:/study/data \
	  -v $(SOLANA_KEYPAIR_DIR):/keys:ro \
	  $(IMAGE) --live --validators 8

clean:
	docker rmi -f $(IMAGE)
