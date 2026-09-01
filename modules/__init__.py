# Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Dynamic lazy attribute resolution in modules package - start
"""
Legacy modules package facade.
Provides lazy re-export resolution for data_access and services modules.
"""

import importlib

_MODULE_MAP = {
    'PostgreSQLConnectionManager': ('data_access.db_connection', 'PostgreSQLConnectionManager'),
    'ActionType': ('data_access.db_connection', 'ActionType'),
    'ActorType': ('data_access.db_connection', 'ActorType'),
    'TicketRepository': ('data_access.ticket_repository', 'TicketRepository'),
    'TicketAnalytics': ('data_access.ticket_analytics', 'TicketAnalytics'),
    'VectorDatabase': ('data_access.vector_db', 'VectorDatabase'),
    'GraphConnector': ('services.connectors.graph_connector', 'GraphConnector'),
    'AzureDevOpsConnector': ('services.connectors.devops_connector', 'AzureDevOpsConnector'),
    'OpenAIAgent': ('services.connectors.openai_agent', 'OpenAIAgent'),
    'DocProcessor': ('services.processors.doc_processor', 'DocProcessor'),
    'TablesProcessor': ('services.processors.tables_processor', 'TablesProcessor'),
    'ImageProcessor': ('services.processors.image_processor', 'ImageProcessor'),
    'redact_pii': ('services.processors.pii_redactor', 'redact_pii'),
    'EnvManager': ('modules.env_manager', 'EnvManager'),
    'SQLLogger': ('modules.sql_logger', 'SQLLogger'),
}

def __getattr__(name: str):
    if name in _MODULE_MAP:
        mod_name, attr_name = _MODULE_MAP[name]
        mod = importlib.import_module(mod_name)
        return getattr(mod, attr_name)
    raise AttributeError(f"module 'modules' has no attribute '{name}'")
# Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Dynamic lazy attribute resolution in modules package - end