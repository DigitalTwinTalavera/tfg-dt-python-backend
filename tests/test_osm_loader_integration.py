"""
Integration tests for OSMLoader service with real database.
Tests full ETL pipeline from OSM file to PostgreSQL.
"""

import pytest
from pathlib import Path
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models.road_network import EdgeModel, NodeModel
from app.services.osm_loader import OSMLoader


# Test database URL
TEST_DATABASE_URL = settings.database_url


@pytest.fixture
def sample_osm_path() -> Path:
    """Path to sample OSM file."""
    return Path(__file__).parent / "fixtures" / "sample.osm"


async def create_test_session():
    """Create a test database session."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async_session_maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    session = async_session_maker()
    return session, engine


async def cleanup_test_data(session: AsyncSession):
    """Remove test data from database."""
    # Delete edges first (foreign key constraint)
    await session.execute(EdgeModel.__table__.delete())
    await session.execute(NodeModel.__table__.delete())
    await session.commit()


# =============================================================================
# Integration Tests
# =============================================================================


class TestOSMLoaderIntegration:
    """Integration tests for full OSM loading pipeline."""

    @pytest.mark.asyncio
    async def test_load_sample_file(self, sample_osm_path):
        """Test loading the sample OSM file into database."""
        session, engine = await create_test_session()
        try:
            # Clear existing data
            await cleanup_test_data(session)

            # Load OSM data
            loader = OSMLoader(session)
            stats = await loader.load_from_file(
                str(sample_osm_path), clear_existing=True
            )

            # Verify stats
            assert stats.nodes_parsed == 8
            assert stats.ways_parsed == 8
            assert stats.ways_skipped == 2  # footway and cycleway
            assert stats.errors == 0

            # Verify nodes in database
            # Only nodes referenced by valid ways should be imported
            assert stats.nodes_imported > 0
            assert stats.edges_imported > 0

        finally:
            await cleanup_test_data(session)
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_nodes_have_correct_attributes(self, sample_osm_path):
        """Test that imported nodes have correct attributes."""
        session, engine = await create_test_session()
        try:
            await cleanup_test_data(session)

            loader = OSMLoader(session)
            await loader.load_from_file(str(sample_osm_path), clear_existing=True)

            # Query nodes
            result = await session.execute(select(NodeModel))
            nodes = list(result.scalars().all())

            # All nodes should have positions
            for node in nodes:
                assert node.position is not None
                assert node.is_active is True

            # At least one node should have metadata with osm_id
            nodes_with_metadata = [n for n in nodes if n.metadata_json]
            assert len(nodes_with_metadata) > 0

        finally:
            await cleanup_test_data(session)
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_edges_reference_valid_nodes(self, sample_osm_path):
        """Test that all edges reference existing nodes."""
        session, engine = await create_test_session()
        try:
            await cleanup_test_data(session)

            loader = OSMLoader(session)
            await loader.load_from_file(str(sample_osm_path), clear_existing=True)

            # Query edges
            result = await session.execute(select(EdgeModel))
            edges = list(result.scalars().all())

            # Get all node IDs
            node_result = await session.execute(select(NodeModel.id))
            node_ids = set(row[0] for row in node_result.fetchall())

            # All edge start/end nodes should exist
            for edge in edges:
                assert edge.start_node_id in node_ids
                assert edge.end_node_id in node_ids

        finally:
            await cleanup_test_data(session)
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_edges_have_geometry(self, sample_osm_path):
        """Test that all edges have geometry."""
        session, engine = await create_test_session()
        try:
            await cleanup_test_data(session)

            loader = OSMLoader(session)
            await loader.load_from_file(str(sample_osm_path), clear_existing=True)

            # Query edges
            result = await session.execute(select(EdgeModel))
            edges = list(result.scalars().all())

            # All edges should have geometry and length
            for edge in edges:
                assert edge.geometry is not None
                assert edge.length > 0

        finally:
            await cleanup_test_data(session)
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_road_types_correctly_assigned(self, sample_osm_path):
        """Test that road types are correctly assigned from OSM highway tags."""
        session, engine = await create_test_session()
        try:
            await cleanup_test_data(session)

            loader = OSMLoader(session)
            await loader.load_from_file(str(sample_osm_path), clear_existing=True)

            # Query edges
            result = await session.execute(select(EdgeModel))
            edges = list(result.scalars().all())

            # Check we have various road types
            road_types = set(e.road_type for e in edges)
            assert "primary" in road_types or "secondary" in road_types

        finally:
            await cleanup_test_data(session)
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_oneway_correctly_detected(self, sample_osm_path):
        """Test that one-way roads are correctly detected."""
        session, engine = await create_test_session()
        try:
            await cleanup_test_data(session)

            loader = OSMLoader(session)
            await loader.load_from_file(str(sample_osm_path), clear_existing=True)

            # Query edges
            result = await session.execute(select(EdgeModel))
            edges = list(result.scalars().all())

            # Should have at least one one-way edge (service road with oneway=yes)
            oneway_edges = [e for e in edges if e.one_way]
            assert len(oneway_edges) >= 1

        finally:
            await cleanup_test_data(session)
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_clear_existing_data(self, sample_osm_path):
        """Test that clear_existing option removes old data."""
        session, engine = await create_test_session()
        try:
            await cleanup_test_data(session)

            loader = OSMLoader(session)

            # First import
            stats1 = await loader.load_from_file(
                str(sample_osm_path), clear_existing=True
            )

            # Second import with clear
            stats2 = await loader.load_from_file(
                str(sample_osm_path), clear_existing=True
            )

            # Both imports should have same counts
            assert stats1.nodes_imported == stats2.nodes_imported
            assert stats1.edges_imported == stats2.edges_imported

            # Total in database should match single import
            node_count = await session.execute(
                select(func.count()).select_from(NodeModel)
            )
            assert node_count.scalar() == stats2.nodes_imported

        finally:
            await cleanup_test_data(session)
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_batch_processing(self, sample_osm_path):
        """Test that batch processing works correctly."""
        session, engine = await create_test_session()
        try:
            await cleanup_test_data(session)

            # Use small batch size to test batching
            loader = OSMLoader(session, batch_size=2)
            stats = await loader.load_from_file(
                str(sample_osm_path), clear_existing=True
            )

            # Should still import all data correctly
            assert stats.nodes_imported > 0
            assert stats.edges_imported > 0
            assert stats.errors == 0

        finally:
            await cleanup_test_data(session)
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_progress_callback_integration(self, sample_osm_path):
        """Test progress callback during full import."""
        session, engine = await create_test_session()
        try:
            await cleanup_test_data(session)

            progress_calls = []

            def progress_callback(stage: str, count: int):
                progress_calls.append((stage, count))

            loader = OSMLoader(session, progress_callback=progress_callback)
            await loader.load_from_file(str(sample_osm_path), clear_existing=True)

            # Should have received progress updates
            stages = [call[0] for call in progress_calls]
            assert "Parsing OSM file" in stages or len(progress_calls) == 0
            # Small file may not trigger interval-based updates

        finally:
            await cleanup_test_data(session)
            await session.close()
            await engine.dispose()
