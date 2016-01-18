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

    def doc_exists(self, docId):
        try:
            result = self.db.get(docId)
        except CouchbaseError as e:
            return False

        return True

    def insert_build_history(self, build, update=False):
        try:
            docId = build['version']+"-"+str(build['build_num'])
            if update:
                result = self.db.upsert(docId, build)
            else:
                result = self.db.insert(docId, build)
            logger.debug("{0}".format(result))
        except CouchbaseError as e:
            if e.rc == 12: 
                logger.warning("Couldn't create build history {0} due to error: {1}".format(docId, e))
                docId = None

        return docId

    def insert_distro_history(self, distro, update=False):
        try:
            docId = distro['version']+"-"+str(distro['build_num'])+"-"+distro['distro']+"-"+distro['edition']
            if update:
                result = self.db.upsert(docId, distro)
            else:
                result = self.db.insert(docId, distro)
            logger.debug("{0}".format(result))
        except CouchbaseError as e:
            if e.rc == 12:
                logger.warning("Couldn't create distro history {0} due to error: {1}".format(docId, e))
                docId = None

        return docId

    def insert_unit_history(self, unit, update=False):
        try:
            docId = unit['version']+"-"+str(unit['build_num'])+"-"+unit['distro']+"-"+unit['edition']+'-tests'
            if update:
                result = self.db.upsert(docId, unit)
            else:
                result = self.db.insert(docId, unit)
            logger.debug("{0}".format(result))
        except CouchbaseError as e:
            if e.rc == 12:
                logger.warning("Couldn't create unit history {0} due to error: {1}".format(docId, e))
                docId = None

        return docId

    def insert_commit(self, commit):
        docId = commit['repo']+"-"+str(commit['sha'])
        inb = commit['in_build'][0]
        try:
            result = self.db.get(docId)
            val = result.value
            if not inb in val['in_build']:
                val['in_build'].append(inb)
                result = self.db.upsert(docId, val)
        except CouchbaseError as e:
            if e.rc == 13:
                try: 
                    result = self.db.insert(docId, commit)
                    logger.debug("{0}".format(result))
                except CouchbaseError as e:
                    print e.rc
                    if e.rc == 12: 
                        logger.error("Couldn't create commit history {0} due to error: {1}".format(docId, e))
                        docId = None

        return docId

    def get_incomplete_builds(self):
        q = N1QLQuery("select url from `build-history` where result is NULL")
        urls = []
        for row in self.db.n1ql_query(q):
            urls.append(row['url'])
        return urls
