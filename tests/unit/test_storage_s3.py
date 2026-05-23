"""Tests for S3 wrappers."""
import boto3
import pytest
from moto import mock_aws

from crawler.storage.s3 import RawHtmlStore


@pytest.fixture
def s3_bucket():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="test-raw")
        yield "test-raw"


def test_put_raw_html_writes_object(s3_bucket):
    store = RawHtmlStore(bucket=s3_bucket)
    uri = store.put_raw_html(
        url_hash="abc123",
        domain="example.com",
        fetched_at_iso="2026-05-23T00:00:00Z",
        html="<html>x</html>",
    )
    assert uri.startswith("s3://test-raw/raw/")
    client = boto3.client("s3", region_name="us-east-1")
    objs = client.list_objects_v2(Bucket=s3_bucket)
    assert objs["KeyCount"] == 1


def test_put_jsonld_writes_blob(s3_bucket):
    store = RawHtmlStore(bucket=s3_bucket)
    uri = store.put_jsonld(
        url_hash="abc123", domain="example.com",
        fetched_at_iso="2026-05-23T00:00:00Z",
        jsonld=[{"@type": "Article"}],
    )
    assert uri.startswith("s3://test-raw/jsonld/")
