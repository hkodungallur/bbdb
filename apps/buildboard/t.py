from db import buildDB
import json

BLDHISTORY_BUCKET = 'couchbase://cb-bbdb:8091/build-history'
bldDB = buildDB(BLDHISTORY_BUCKET)

#bldDB.run_query("select build_num from `build-history` where type='top_level_build'")
#bldDB.run_query("select count(*) from `build-history` where type='commit' and repo != 'testrunner'")

#rel_lines = bldDB.get_release_lines('watson')
#print rel_lines

ret=bldDB.get_recent_builds('4.7.0')
print json.dumps(ret, indent=2)

#res = bldDB.get_long_history(1420,3)
#print json.dumps(res, indent=2)

#res = bldDB.get_recent_history(1420,3)
#print json.dumps(res, indent=2)

#rl=bldDB.get_release_lines()
#print rl

