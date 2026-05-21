"""Check EarnKaro affiliate URL status — reports placeholders vs real URLs."""
from pathlib import Path

import yaml

catalog_path = Path(__file__).parent.parent / "genlab-core" / "config" / "affiliate_catalog.yaml"

with open(catalog_path) as f:
    catalog = yaml.safe_load(f)

placeholder_count = 0
real_count = 0
placeholder_products = []

for niche_id, niche_data in catalog.get("niches", {}).items():
    for product in niche_data.get("products", []):
        if not product.get("enabled", True):
            continue
        for net_name, net_data in product.get("networks", {}).items():
            url = net_data.get("url", "")
            if "example.com" in url:
                placeholder_count += 1
                placeholder_products.append(f"{niche_id}/{product['name']} → {net_name}")
            elif url:
                real_count += 1

print(f"Real URLs: {real_count}")
print(f"Placeholder URLs: {placeholder_count}")
print()
if placeholder_products:
    print("Placeholder products (need real affiliate URLs):")
    for p in placeholder_products:
        print(f"  • {p}")
