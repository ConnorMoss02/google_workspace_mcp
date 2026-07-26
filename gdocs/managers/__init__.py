"""
Google Docs Operation Managers

This package provides high-level manager classes for complex Google Docs operations,
extracting business logic from the main tools module to improve maintainability.
"""

from .batch_operation_manager import BatchOperationManager
from .header_footer_manager import HeaderFooterManager
from .table_operation_manager import TableOperationManager
from .validation_manager import ValidationManager

__all__ = [
    "BatchOperationManager",
    "HeaderFooterManager",
    "TableOperationManager",
    "ValidationManager",
]
