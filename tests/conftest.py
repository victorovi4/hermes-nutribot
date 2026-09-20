import sys
import types

if "tools.registry" not in sys.modules:
    tools = types.ModuleType("tools")
    registry = types.ModuleType("tools.registry")
    registry.tool_error = lambda message: '{"success":false,"error":"' + str(message).replace('"', "'") + '"}'
    tools.registry = registry
    sys.modules["tools"] = tools
    sys.modules["tools.registry"] = registry
