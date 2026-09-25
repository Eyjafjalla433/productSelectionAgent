"""Run from repository root: python -m description_module.demo"""
import json
from . import DescriptionComparison

HANDOFF = {
    'schema_version': 'show-me-your-agent.selection.v1',
    'session_id': 'synthetic-demo', 'intent_version': 1,
    'requirements': {'state_version': 1, 'hard': {'category': 'tshirt'},
                     'soft': {'material': ['cotton']}, 'excluded': {}},
    'selected_products': [
        {'parent_asin': 'DEMO-A', 'title': 'Cotton T-shirt',
         'product_description': [], 'product_bullet_points': ['Soft cotton fabric'],
         'details': {'Brand': 'Demo brand', 'Color': 'Black'}, 'price': None},
        {'parent_asin': 'DEMO-B', 'title': 'Polyester T-shirt',
         'product_description': ['Polyester shirt with a relaxed fit.'],
         'product_bullet_points': [], 'details': {'Color': 'Blue'}, 'price': None},
    ],
}

if __name__ == '__main__':
    print(json.dumps(DescriptionComparison().compare(HANDOFF).to_dict(), ensure_ascii=False, indent=2))
