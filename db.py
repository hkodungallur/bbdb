#!/usr/bin/python

import os
import logging

from couchbase.bucket import Bucket
from couchbase.n1ql import N1QLQuery
from couchbase.views.params import Query
from couchbase.bucket import LOCKMODE_WAIT
from couchbase.exceptions import CouchbaseError, KeyExistsError, NotFoundError
from couchbase.views.iterator import RowProcessor


logger = logging.getLogger()

class DB(object):
    def __init__(self, bucket):
        self.bucket = bucket
        self.db = Bucket(bucket, lockmode=LOCKMODE_WAIT)

    def insert_build_history(self, build):
        #
        # param: bldHistory
        # type: dict
        #
        # Job history should be inserted prior to this
        #
        try:
            docId = build['version']+"-"+str(build['build_num'])
            result = self.db.insert(docId, build)
            logger.debug("{0}".format(result))
        except CouchbaseError as e:
            if e.rc == 12: 
                logger.warning("Couldn't create build history {0} due to error: {1}".format(docId, e))

        return docId

    def insert_commit(self, commit):
        try:
            docId = commit['repo']+"-"+str(commit['sha'])
            result = self.db.insert(docId, commit)
            logger.debug("{0}".format(result))
        except CouchbaseError as e:
            print e.rc
            if e.rc == 12: 
                logger.error("Couldn't create commit history {0} due to error: {1}".format(docId, e))

        return docId
