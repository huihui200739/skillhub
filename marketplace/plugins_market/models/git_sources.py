# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from sqlalchemy import BigInteger, Column, Index, String, Text, UniqueConstraint

from .base import Base


class GitSourceDB(Base):
    """用户注册的 Git 仓库源；表结构见 sql/baseline/openjiuwen_market/DDL/git_sources.sql。"""

    __tablename__ = "git_sources"

    id = Column(String(64), primary_key=True, nullable=False)
    name = Column(String(128), nullable=False)
    repo_url = Column(String(512), nullable=False)
    repo_url_canonical = Column(String(640), nullable=True)
    git_source_dedup_key = Column(String(64), nullable=True)
    ref = Column(String(256), nullable=False)
    skills_subpath = Column(String(512), nullable=True)
    visibility_scope = Column(String(32), nullable=False)
    created_by_user_id = Column(String(64), nullable=False, index=True)
    create_time_ms = Column(BigInteger, nullable=False)
    update_time_ms = Column(BigInteger, nullable=False)
    last_index_status = Column(String(64), nullable=True)
    last_index_error = Column(Text, nullable=True)
    last_indexed_at_ms = Column(BigInteger, nullable=True)

    __table_args__ = (
        Index("idx_gs_visibility", "visibility_scope"),
        Index("idx_gs_repo_url_canonical", "repo_url_canonical"),
        UniqueConstraint("git_source_dedup_key", name="uk_git_source_dedup_key"),
    )
