from rest_framework.pagination import CursorPagination, PageNumberPagination


class DefaultCursorPagination(CursorPagination):
    """Default for collections: stable under concurrent inserts and efficient on large tables.

    Ordered by the UUIDv7 primary key, which is unique and time-ordered, so newest first.
    Response shape: ``{"next": ..., "previous": ..., "results": [...]}``.
    """

    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100
    ordering = "-id"


class SmallListPagination(PageNumberPagination):
    """For short, human-sized lists (branches, roles, members) usually shown whole.
    Response shape: ``{"count", "next", "previous", "results"}``."""

    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 500
