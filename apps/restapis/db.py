#!/usr/bin/python
import os
import logging
import time
from random import shuffle

from couchbase.bucket import Bucket
from couchbase.n1ql import N1QLQuery
from couchbase.views.params import Query
from couchbase.bucket import LOCKMODE_WAIT
from couchbase.exceptions import CouchbaseError, KeyExistsError, NotFoundError
from couchbase.views.iterator import RowProcessor


logger = logging.getLogger()

class DB(object):
    def __init__(self):
        vm_bucket = 'couchbase://cb-bbdb:8091/default'
        bld_bucket = 'couchbase://cb-bbdb:8091/build-history'

        self.vmdb = Bucket(vm_bucket, lockmode=LOCKMODE_WAIT)
        self.blddb = Bucket(bld_bucket, lockmode=LOCKMODE_WAIT)

    def get_bld_doc(self, ver, bld):
        docid = '{}-{}'.format(ver, bld)
        try:
            result = self.blddb.get(docid)
        except CouchbaseError as e:
            return {}

        return result.value

    def get_doc(self, docid):
        try:
            result = self.blddb.get(docid)
        except CouchbaseError as e:
            return {}

        return result.value

    def qe_kicked_off(self, ver):
        try:
            result = self.blddb.get(ver)
            doc = result.value
            doc['qe_sanity'] = 'true'
            result = self.blddb.upsert(ver, doc)
        except CouchbaseError as e:
            return ''

        return ver

    def get_vm_doc(self, ip):
        try:
            result = self.vmdb.get(ip)
        except CouchbaseError as e:
            return False

        return result

    def insert_vm(self, vm):
        try:
            docId = vm['ip']
            result = self.vmdb.upsert(docId, vm)
        except CouchbaseError as e:
            if e.rc == 12: 
                logger.warning("Couldn't create build history {0} due to error: {1}".format(docId, e))
                docId = None

        return docId

    def update_vm_state(self, ip, state, who):
        try:
            doc = self.get_vm_doc(ip).value
            doc['state'] = state
            doc['who'] = who
            if state == 'reserved':
                doc['expires'] = int(time.time() + 3*60*60)
            elif state == 'available':
                doc['expires'] = 0
            result = self.vmdb.upsert(ip, doc)
        except CouchbaseError as e:
            if e.rc == 12: 
                logger.warning("Couldn't create build history {0} due to error: {1}".format(docId, e))
                doc = None

        return doc

    def provision(self, plat, count, purpose, who):
        curtime = int(time.time())
        getmore = '10'
        if int(count) > 10:
            getmore = count
        q = N1QLQuery("SELECT ip FROM `default` WHERE (state = 'available' OR expires < {} ) AND os = '{}' AND '{}' IN purpose LIMIT {}".format(curtime, plat, purpose, getmore))
        vms = []
        for row in self.vmdb.n1ql_query(q):
            vms.append(row['ip'])
        if len(vms) < int(count):
            return []
        shuffle(vms)
        ret_vms = vms[:int(count)]
        for ip in ret_vms:
            self.update_vm_state(ip, 'reserved', who)
        return ret_vms

    def release(self, vms):
        for ip in vms:
            self.update_vm_state(ip, 'available', '')
        return vms

    def last_sanity(self, version, result='0'):
        res_q = "sanity_result IS NOT MISSING"
        if result == '1':
            res_q = "sanity_result = 'PASSED'"
        q = N1QLQuery("SELECT build_num FROM `build-history` WHERE type = 'top_level_build' AND {} AND version = '{}' ORDER BY build_num DESC LIMIT 1".format(res_q, version))
        bnums = []
        for row in self.blddb.n1ql_query(q):
            bnums.append(row['build_num'])
        if bnums:
            return bnums[0]
        else:
            return 0

    def last_unit(self, version):
        q = N1QLQuery("SELECT build_num FROM `build-history` WHERE type = 'top_level_build' AND unit = 'true' AND version = '{}' ORDER BY build_num DESC LIMIT 1".format(version))
        bnums = []
        for row in self.blddb.n1ql_query(q):
            bnums.append(row['build_num'])
        if bnums:
            return bnums[0]
        else:
            return 0

    def last_unit_plus_sanity(self, version):
        q = N1QLQuery("SELECT build_num FROM `build-history` WHERE type = 'top_level_build' AND unit = 'true' AND sanity_result = 'PASSED' AND version = '{}' ORDER BY build_num DESC LIMIT 1".format(version))
        bnums = []
        for row in self.blddb.n1ql_query(q):
            bnums.append(row['build_num'])
        if bnums:
            return bnums[0]
        else:
            return 0

    def last_qe(self, version):
        q = N1QLQuery("SELECT max(build_num) FROM `build-history` WHERE type = 'top_level_build' AND qe_sanity = 'true' AND version = '{}'".format(version))
        bnums = []
        for row in self.blddb.n1ql_query(q):
            bnums.append(row['$1'])
            break
        if bnums:
            return bnums[0]
        else:
            return 0

    def not_yet_sanity_tested(self, version, limit=3):
        frm = self.last_sanity(version)
        q = N1QLQuery("SELECT build_num FROM `build-history` WHERE type = 'top_level_build' AND build_num > {} AND failed = [] AND incomplete = [] AND sanity_result IS MISSING AND version = '{}' ORDER BY build_num DESC LIMIT {}".format(frm, version, limit))
        bnums = []
        for row in self.blddb.n1ql_query(q):
            bnums.append(row['build_num'])
        print 'not_yet_sanity_test: ',
        print bnums
        return bnums

    def not_yet_unit_tested(self, version, limit=3):
        frm = self.last_unit(version)
        print frm
        print "SELECT build_num FROM `build-history` WHERE type = 'top_level_build' AND build_num > {} AND unit_result IS MISSING AND failed = [] AND incomplete = [] AND version = '{}' ORDER BY build_num DESC LIMIT {}".format(frm, version, limit)
        q = N1QLQuery("SELECT build_num FROM `build-history` WHERE type = 'top_level_build' AND build_num > {} AND unit_result IS MISSING AND version = '{}' ORDER BY build_num DESC LIMIT {}".format(frm, version, limit))
        bnums = []
        for row in self.blddb.n1ql_query(q):
            bnums.append(row['build_num'])
        return bnums

    def get_log(self, version, from_build, to_build):
        q = N1QLQuery("SELECT b.build_num, c.* FROM `build-history` AS b JOIN `build-history` AS c ON KEYS b.commits WHERE b.type = 'top_level_build' AND c.type = 'commit' AND b.version = '{0}' AND tonumber(b.build_num) > {1} AND tonumber(b.build_num) <={2}".format(version, from_build, to_build))
        print q
        log = []
        for row in self.blddb.n1ql_query(q):
            log.append(row)
        return log

    def fixes_in_build(self, ver, bnum):
        # Make this a N1QL query
        tix = []
        bdoc = self.get_bld_doc(ver, bnum)
        if bdoc:
            if bdoc.has_key('commits'):
                for c in bdoc['commits']:
                    cdoc = self.get_doc(c)
                    if cdoc:
                        if cdoc.has_key('fixes'):
                            tix = tix + cdoc['fixes']
        return tix
                    

    def ticket_in_build(self, tid):
        q = N1QLQuery("SELECT ARRAY_AGG(z) AS in_build FROM `build-history` b UNNEST in_build z WHERE Array x FOR x IN b.fixes WHEn x = '{}' END;".format(tid))
        bnums = []
        for row in self.blddb.n1ql_query(q):
            inb = row['in_build']
            if inb:
                bnums = list(set(inb))
            break
        return bnums
