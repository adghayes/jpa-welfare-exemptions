"""
Find Parcels Module

Find all parcels associated with an address in LA County.
"""

from .finder import find_parcels, FindParcelsResult

__all__ = ['find_parcels', 'FindParcelsResult']
