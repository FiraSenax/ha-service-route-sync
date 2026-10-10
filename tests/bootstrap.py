"""Load router-independent modules without importing Home Assistant."""

import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
package = types.ModuleType("service_route_sync")
package.__path__ = [str(ROOT / "custom_components" / "service_route_sync")]
sys.modules.setdefault("service_route_sync", package)
