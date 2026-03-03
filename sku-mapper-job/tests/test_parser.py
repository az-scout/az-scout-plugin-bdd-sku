"""Unit tests for the SKU name parser."""

from sku_mapper_job.parser import SkuInfo, parse_sku


class TestParseSkuStandard:
    """Test cases for Standard_… SKU names."""

    def test_d2s_v5(self) -> None:
        info = parse_sku("Standard_D2s_v5")
        assert info.tier == "Standard"
        assert info.family == "D"
        assert info.vcpus == 2
        assert info.version == "v5"
        assert info.category == "general"
        assert "premium-storage" in info.workload_tags

    def test_d4ds_v5(self) -> None:
        info = parse_sku("Standard_D4ds_v5")
        assert info.tier == "Standard"
        assert info.family == "D"
        assert info.vcpus == 4
        assert info.version == "v5"
        assert info.category == "general"
        assert "local-disk" in info.workload_tags
        assert "premium-storage" in info.workload_tags

    def test_e8ds_v5(self) -> None:
        info = parse_sku("Standard_E8ds_v5")
        assert info.tier == "Standard"
        assert info.family == "E"
        assert info.vcpus == 8
        assert info.version == "v5"
        assert info.category == "memory"

    def test_e64is_v5(self) -> None:
        info = parse_sku("Standard_E64is_v5")
        assert info.tier == "Standard"
        assert info.family == "E"
        assert info.vcpus == 64
        assert info.version == "v5"
        assert info.category == "memory"
        assert "isolated" in info.workload_tags

    def test_f4s_v2(self) -> None:
        info = parse_sku("Standard_F4s_v2")
        assert info.tier == "Standard"
        assert info.family == "F"
        assert info.vcpus == 4
        assert info.version == "v2"
        assert info.category == "compute"

    def test_l8s(self) -> None:
        info = parse_sku("Standard_L8s")
        assert info.tier == "Standard"
        assert info.family == "L"
        assert info.vcpus == 8
        assert info.version is None
        assert info.category == "storage"

    def test_b2s(self) -> None:
        info = parse_sku("Standard_B2s")
        assert info.tier == "Standard"
        assert info.family == "B"
        assert info.vcpus == 2
        assert info.version is None
        assert info.category == "burstable"

    def test_nc6(self) -> None:
        info = parse_sku("Standard_NC6")
        assert info.tier == "Standard"
        assert info.family == "NC"
        assert info.vcpus == 6
        assert info.version is None
        assert info.category == "gpu"

    def test_nc24ads_a100_v4(self) -> None:
        info = parse_sku("Standard_NC24ads_A100_v4")
        assert info.tier == "Standard"
        assert info.family == "NC"
        assert info.vcpus == 24
        assert info.version == "v4"
        assert info.category == "gpu"
        assert "amd" in info.workload_tags
        assert "local-disk" in info.workload_tags

    def test_nd96isr_h100_v5(self) -> None:
        info = parse_sku("Standard_ND96isr_H100_v5")
        assert info.tier == "Standard"
        assert info.family == "ND"
        assert info.vcpus == 96
        assert info.version == "v5"
        assert info.category == "gpu"
        assert "isolated" in info.workload_tags
        assert "rdma" in info.workload_tags

    def test_hb120rs_v3(self) -> None:
        info = parse_sku("Standard_HB120rs_v3")
        assert info.tier == "Standard"
        assert info.family == "HB"
        assert info.vcpus == 120
        assert info.version == "v3"
        assert info.category == "hpc"
        assert "rdma" in info.workload_tags

    def test_m128ms(self) -> None:
        info = parse_sku("Standard_M128ms")
        assert info.tier == "Standard"
        assert info.family == "M"
        assert info.vcpus == 128
        assert info.version is None
        assert info.category == "memory"
        assert "memory-intensive" in info.workload_tags

    def test_dc2s_v2(self) -> None:
        info = parse_sku("Standard_DC2s_v2")
        assert info.tier == "Standard"
        assert info.family == "DC"
        assert info.vcpus == 2
        assert info.version == "v2"
        # DC maps via D-prefix → general
        assert info.category == "general"

    def test_l8s_v3(self) -> None:
        info = parse_sku("Standard_L8s_v3")
        assert info.tier == "Standard"
        assert info.family == "L"
        assert info.vcpus == 8
        assert info.version == "v3"
        assert info.category == "storage"

    def test_nv36ads_a10_v5(self) -> None:
        info = parse_sku("Standard_NV36ads_A10_v5")
        assert info.tier == "Standard"
        assert info.family == "NV"
        assert info.vcpus == 36
        assert info.version == "v5"
        assert info.category == "gpu"

    def test_hc44rs(self) -> None:
        info = parse_sku("Standard_HC44rs")
        assert info.tier == "Standard"
        assert info.family == "HC"
        assert info.vcpus == 44
        assert info.version is None
        assert info.category == "hpc"
        assert "rdma" in info.workload_tags


class TestParseSkuNonStandard:
    """Test cases for SKU names that don't match the Standard_ pattern."""

    def test_non_standard_prefix(self) -> None:
        info = parse_sku("Basic_A1")
        assert info.tier is None
        assert info.family is None
        assert info.category == "other"

    def test_empty_string(self) -> None:
        info = parse_sku("")
        assert info.tier is None
        assert info.family is None
        assert info.category == "other"

    def test_garbage(self) -> None:
        info = parse_sku("not_a_sku_at_all")
        assert info.tier is None
        assert info.category == "other"


class TestSkuInfoToDict:
    """Verify SkuInfo conversion to row dict for upserts."""

    def test_sku_info_fields(self) -> None:
        info = SkuInfo(
            sku_name="Standard_D2s_v5",
            tier="Standard",
            family="D",
            series="Dsv5",
            version="v5",
            vcpus=2,
            sku_type="Dsv5",
            category="general",
            workload_tags=["premium-storage"],
        )
        assert info.sku_name == "Standard_D2s_v5"
        assert info.tier == "Standard"
        assert info.family == "D"
        assert info.series == "Dsv5"
        assert info.version == "v5"
        assert info.vcpus == 2
        assert info.sku_type == "Dsv5"
        assert info.category == "general"
        assert info.workload_tags == ["premium-storage"]


class TestSkuType:
    """Test the derived sku_type field."""

    def test_dsv5(self) -> None:
        info = parse_sku("Standard_D2s_v5")
        assert info.sku_type == "Dsv5"

    def test_edsv5(self) -> None:
        info = parse_sku("Standard_E8ds_v5")
        assert info.sku_type == "Edsv5"

    def test_nc_no_version(self) -> None:
        info = parse_sku("Standard_NC6")
        assert info.sku_type == "NC"

    def test_hb_rsv3(self) -> None:
        info = parse_sku("Standard_HB120rs_v3")
        assert info.sku_type == "HBrsv3"
