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

        return result

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

    def insert_test_history(self, unit, test_type='unit', update=False):
        try:
            if test_type == 'unit':
                docId = unit['version']+"-"+str(unit['build_num'])+"-"+unit['distro']+"-"+unit['edition']+'-tests'
            elif test_type == 'build_sanity':
                docId = unit['version']+"-"+str(unit['build_num'])+"-"+unit['distro']+"-"+unit['edition']+'-sanity-tests'

            if update:
                result = self.db.upsert(docId, unit)
            else:
                result = self.db.insert(docId, unit)
            logger.debug("{0}".format(result))
        except CouchbaseError as e:
            if e.rc == 12:
                logger.warning("Couldn't create test history {0} due to error: {1}".format(docId, e))
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

    def update_distro_result(self, docId, distroId, result):
        try:
            ret = self.db.get(docId).value
            if not distroId in ret[result]:
                ret[result].append(distroId)
            if result != 'incomplete':
                if distroId in ret['incomplete']:
                    ret['incomplete'].remove(distroId)
            self.db.upsert(docId, ret)
            logger.debug("{0}".format(result))
        except CouchbaseError as e:
            logger.warning("Couldn't update distro result on {0} due to error: {1}".format(docId, e))
            docId = None

        return

    def get_incomplete_builds(self):
        q = N1QLQuery("select url from `build-history` where result is NULL")
        urls = []
        for row in self.db.n1ql_query(q):
            urls.append(row['url'])
        return urls

    def get_incomplete_sanity_runs(self):
        q = N1QLQuery("select sanity_url from `build-history` where type = 'top_level_build' and sanity_result = 'INCOMPLETE'")
        urls = []
        for row in self.db.n1ql_query(q):
            urls.append(row['sanity_url'])
        return urls

    def get_incomplete_unit_runs(self):
        q = N1QLQuery("select unit_urls from `build-history` where type = 'top_level_build' and unit_result = 'INCOMPLETE'")
        urls = []
        for row in self.db.n1ql_query(q):
            ulist = row['unit_urls']
            for u in ulist:
                if u['result'] == 'INCOMPLETE':
                    urls.append(u['url'])
        return urls
