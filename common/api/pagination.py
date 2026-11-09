from rest_framework.pagination import CursorPagination


class DefaultCursorPagination(CursorPagination):
    """Default for collections: stable under concurrent inserts and efficient on large tables.

    Ordered by the UUIDv7 primary key, which is unique and time-ordered, so newest first.
    Response shape: ``{"next": ..., "previous": ..., "results": [...]}``.
    """

    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100
    ordering = "-id"
