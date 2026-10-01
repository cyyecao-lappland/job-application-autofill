"""Local-only tool exposure contract test, no account inference."""
import json
from edge_form_graph.isolation import ensure_isolated

if __name__ == "__main__":
    print(json.dumps(ensure_isolated(), ensure_ascii=False, indent=2))
