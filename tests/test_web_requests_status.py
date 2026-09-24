from collection_web.requests_status import progress_for
from collection_web.integrations import title_metadata
from collection_web.store import DomainError
from unittest.mock import patch
import pytest

ITEM={"title":"Film","year":2000,"media_type":"movie"}
CATALOG=[{"id":7,"title":"Film","year":2000,"hasFile":False}]

def test_queue_download_import_and_plex_wait():
    assert progress_for(ITEM,"movie",CATALOG,[{"movieId":7,"status":"downloading","size":100,"sizeleft":25}])=="Downloading 75%"
    assert progress_for(ITEM,"movie",CATALOG,[{"movieId":7,"status":"completed"}])=="Importing"
    assert progress_for(ITEM,"movie",[{**CATALOG[0],"hasFile":True}],[])=="Downloaded; waiting for Plex"
    assert "attention" in progress_for(ITEM,"movie",CATALOG,[{"movieId":7,"trackedDownloadState":"importBlocked"}])
    assert progress_for(ITEM,"movie",CATALOG,[])=="Requested; waiting for a release"

def test_metadata_outage_is_not_an_unresolved_identity():
    with patch("collection_web.integrations.arr_request",side_effect=DomainError("Unavailable")):
        with pytest.raises(DomainError,match="temporarily unavailable"):
            title_metadata({},ITEM,strict=True)
        assert title_metadata({},ITEM) is None
    with patch("collection_web.integrations.arr_request",return_value=[]):
        assert title_metadata({},ITEM,strict=True) is None
