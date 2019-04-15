# Some infos and notes:

+ Requirements:
- OS: Any with python 2.7 and libmysqlclient-dev
- RAM: 512MB - 1 GB should be fine
- CPU: Xeon E3-1240v3 (4c/8t): all threads @ 100% while doing 400-450 Mbps I/O
- with 1 config per core and haproxy to distribute connections among processes.

+ mysql: user auth with connlimit and expiration. password hashs = sha256.
+ mysql: user and backend traffic stats and established backends/sessions.
+ supports multiple providers/accounts. set equal provider accs to same `bgrp` and set different `name`.
+ if article not found: 1 user-connection establishs 1 connection to every backend (but only 1 in same `bgrp`) while searching!
+ DEBUG_LEVEL: 1 - 4 is almost silent, 5 will show some usefull info and 6 - 9 will spam lots of debugs.
+ telnet admin interface: set adminpwd in config file and connect 'telnet localhost 11119'
+ telnet commands are 1-liner like: ADMIN AUTH myPASSWORD $COMMAND $VALUE
    
    # change debug level
    ~: ADMIN AUTH myPASSWORD DEBUG 5
    
    # show some information
    ~: ADMIN AUTH myPASSWORD INFO
    
    # close the process
    ~: ADMIN AUTH myPASSWORD CLOSE
    
    # re-open the process
    ~: ADMIN AUTH myPASSWORD OPEN
    
    # shutdown the process
    ~: ADMIN AUTH myPASSWORD SHUTDOWN
    
    # print debug values to logfile
    ~: ADMIN AUTH myPASSWORD PRINT

+ you should search for '# hack to select provider on first connect' and set tback

+ to be continued...
