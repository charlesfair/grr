#!/usr/bin/env python
"""MultiTypeCollection implementation."""

from grr.lib import aff4
from grr.lib import data_store
from grr.lib import rdfvalue

from grr.lib.aff4_objects import sequential_collection

from grr.lib.rdfvalues import flows as rdf_flows


class MultiTypeCollection(aff4.AFF4Object):
  """A collection that stores multiple types of data in per-type sequences."""

  VALUE_TYPE_PREFIX = "aff4:value_type_"

  @classmethod
  def StaticAdd(cls,
                collection_urn,
                token,
                rdf_value,
                timestamp=None,
                suffix=None,
                mutation_pool=None,
                **kwargs):
    """Adds an rdf value to a collection.

    Adds an rdf value to a collection. Does not require that the collection be
    open. NOTE: The caller is responsible for ensuring that the collection
    exists and is of the correct type.

    Args:
      collection_urn: The urn of the collection to add to.

      token: The database access token to write with.

      rdf_value: The rdf value to add to the collection. If this value is not
          GrrMessage, it will be wrapped into GrrMessage (later when
          collection is iterated, this value will still be returned wrapped
          in GrrMessage).

      timestamp: The timestamp (in microseconds) to store the rdf value
          at. Defaults to the current time.

      suffix: A 'fractional timestamp' suffix to reduce the chance of
          collisions. Defaults to a random number.

      mutation_pool: An optional MutationPool object to write to. If not given,
                     the data_store is used directly.

      **kwargs: Keyword arguments to pass through to the underlying database
        call.

    Returns:
      The pair (timestamp, suffix) which identifies the value within the
      collection.

    Raises:
      ValueError: rdf_value has unexpected type.

    """
    if rdf_value is None:
      raise ValueError("Can't add None to MultiTypeCollection")

    if not isinstance(rdf_value, rdf_flows.GrrMessage):
      rdf_value = rdf_flows.GrrMessage(payload=rdf_value)

    value_type = rdf_value.args_rdf_name or rdf_flows.GrrMessage.__name__

    # In order to make this fast, we never actually generate the
    # subcollections, we just use them. This means that we cannot use
    # ListChildren to get all the items stored in this
    # MultiTypeCollection.
    subpath = collection_urn.Add(value_type)
    sequential_collection.GrrMessageCollection.StaticAdd(
        subpath,
        token,
        rdf_value,
        timestamp=timestamp,
        suffix=suffix,
        mutation_pool=mutation_pool,
        **kwargs)

    if mutation_pool:
      mutation_pool.Set(collection_urn,
                        "%s%s" % (cls.VALUE_TYPE_PREFIX, value_type),
                        1,
                        timestamp=0,
                        **kwargs)
    else:
      data_store.DB.Set(collection_urn,
                        "%s%s" % (cls.VALUE_TYPE_PREFIX, value_type),
                        1,
                        timestamp=0,
                        token=token,
                        **kwargs)

  def ListStoredTypes(self):
    res = []
    for attribute, _, _ in data_store.DB.ResolveRow(self.urn, token=self.token):
      if attribute.startswith(self.VALUE_TYPE_PREFIX):
        res.append(attribute[len(self.VALUE_TYPE_PREFIX):])
    return res

  def ScanByType(self,
                 type_name,
                 after_timestamp=None,
                 include_suffix=False,
                 max_records=None):
    """Scans for stored records.

    Scans through the collection, returning stored values ordered by timestamp.

    Args:
      type_name: Type of the records to scan.

      after_timestamp: If set, only returns values recorded after timestamp.

      include_suffix: If true, the timestamps returned are pairs of the form
        (micros_since_epoc, suffix) where suffix is a 24 bit random refinement
        to avoid collisions. Otherwise only micros_since_epoc is returned.

      max_records: The maximum number of records to return. Defaults to
        unlimited.

    Yields:
      Pairs (timestamp, rdf_value), indicating that rdf_value was stored at
      timestamp.

    """
    sub_collection_urn = self.urn.Add(type_name)
    sub_collection = aff4.FACTORY.Create(
        sub_collection_urn,
        aff4_type=sequential_collection.GrrMessageCollection,
        token=self.token)
    for item in sub_collection.Scan(
        after_timestamp=after_timestamp,
        include_suffix=include_suffix,
        max_records=max_records):
      yield item

  def LengthByType(self, type_name):
    sub_collection_urn = self.urn.Add(type_name)
    sub_collection = aff4.FACTORY.Create(
        sub_collection_urn,
        aff4_type=sequential_collection.GrrMessageCollection,
        token=self.token)
    return len(sub_collection)

  def Add(self, rdf_value, timestamp=None, suffix=None, **kwargs):
    """Adds an rdf value to the collection.

    Adds an rdf value to the collection. Does not require that the collection
    be locked.

    Args:
      rdf_value: The rdf value to add to the collection. If this value is not
          GrrMessage, it will be wrapped into GrrMessage (later when
          collection is iterated, this value will still be returned wrapped
          in GrrMessage).

      timestamp: The timestamp (in microseconds) to store the rdf value
          at. Defaults to the current time.

      suffix: A 'fractional timestamp' suffix to reduce the chance of
          collisions. Defaults to a random number.

      **kwargs: Keyword arguments to pass through to the underlying database
        call.

    Returns:
      The pair (timestamp, suffix) which identifies the value within the
      collection.

    Raises:
      ValueError: rdf_value has unexpected type.

    """
    return self.StaticAdd(
        self.urn,
        self.token,
        rdf_value,
        timestamp=timestamp,
        suffix=suffix,
        **kwargs)

  def __iter__(self):
    sub_collection_urns = [
        self.urn.Add(stored_type) for stored_type in self.ListStoredTypes()
    ]
    for sub_collection_urn in sub_collection_urns:
      sub_collection = aff4.FACTORY.Create(
          sub_collection_urn,
          aff4_type=sequential_collection.GrrMessageCollection,
          token=self.token)
      for item in sub_collection:
        yield item

  def __len__(self):
    l = 0
    sub_collection_urns = [
        self.urn.Add(stored_type) for stored_type in self.ListStoredTypes()
    ]
    for sub_collection_urn in sub_collection_urns:
      sub_collection = aff4.FACTORY.Create(
          sub_collection_urn,
          aff4_type=sequential_collection.GrrMessageCollection,
          token=self.token)
      l += len(sub_collection)

    return l

  def OnDelete(self, deletion_pool=None):
    super(MultiTypeCollection, self).OnDelete(deletion_pool=deletion_pool)

    for urn, _, _ in data_store.DB.ScanAttribute(
        self.urn,
        sequential_collection.SequentialCollection.ATTRIBUTE,
        token=self.token):
      deletion_pool.MarkForDeletion(rdfvalue.RDFURN(urn))
