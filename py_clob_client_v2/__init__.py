import sys
import py_clob_client
import py_clob_client.client as _client
import py_clob_client.clob_types as _clob_types
import py_clob_client.order_builder as _order_builder
import py_clob_client.order_builder.constants as _constants

# Alias submodules
client = _client
clob_types = _clob_types
order_builder = _order_builder
order_builder.constants = _constants

# Provide missing attribute for compatibility
if not hasattr(_clob_types, 'OrderArgsV2'):
    setattr(_clob_types, 'OrderArgsV2', _clob_types.OrderArgs)

# Some older code expects create_or_derive_api_key on ClobClient
if not hasattr(_client.ClobClient, 'create_or_derive_api_key'):
    setattr(_client.ClobClient, 'create_or_derive_api_key', _client.ClobClient.create_or_derive_api_creds)

if not hasattr(_client.ClobClient, 'get_open_orders'):
    setattr(_client.ClobClient, 'get_open_orders', _client.ClobClient.get_orders)


# Register the compatibility package in sys.modules
sys.modules['py_clob_client_v2'] = sys.modules[__name__]
sys.modules['py_clob_client_v2.client'] = _client
sys.modules['py_clob_client_v2.clob_types'] = _clob_types
sys.modules['py_clob_client_v2.order_builder'] = _order_builder
sys.modules['py_clob_client_v2.order_builder.constants'] = _constants
