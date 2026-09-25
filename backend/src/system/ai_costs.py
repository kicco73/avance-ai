from __future__ import annotations

COST_CURRENCY = "EUR"
TOKENS_PER_MILLION = 1_000_000


class AiCostPricing(object):

    def __init__(self, services_config: dict) -> None:
        self._prices = {
            f"{provider['driver']}/{provider['model']}": provider
            for provider in services_config.get("ai", {}).get("providers", [])
        }

    def cost(self, row: dict) -> float | None:
        price = self._prices.get(row["provider_label"])
        if price is None:
            return None
        return (
            row["input_tokens"] * price["input-token-ppm"]
            + row["output_tokens"] * price["output-token-ppm"]
            + row["thoughts_tokens"] * price["thought-token-ppm"]
        ) / TOKENS_PER_MILLION

    def daily(self, rows: list[dict]) -> dict:
        history: dict[str, dict[str, float]] = {}
        unpriced: set[str] = set()
        for row in rows:
            cost = self.cost(row)
            if cost is None:
                unpriced.add(row["provider_label"])
                continue
            history.setdefault(row["day"], {})[row["provider_label"]] = cost
        return {
            "currency": COST_CURRENCY,
            "history": [{"timestamp": f"{day}T00:00:00Z", "values": values} for day, values in sorted(history.items())],
            "unpriced_providers": sorted(unpriced),
        }
