iNCIPit
=======

Inital version of ncip v.1 ncip responder for evergreen / open-ils

Initial configuration
---------------------

Setup the default configuration file:

```
cp iNCIPit-example.ini iNCIPit.ini # edit as necessary
```

Optionally, per request hostname configuration files can be used. For example:

- https://target.host/iNCIPit.cgi # REQUEST URL
- target.host # HOSTNAME
- target.host.ini # CONFIGURATION FILE

```
cp iNCIPit-example.ini target.host.ini # edit as necessary
```

Testing
-------

you can initiate / test with the following:

```
curl -v --insecure -H 'Content-Type:text/xml' --data @NCIPmsgs/LookupUser.ncip -X POST 'https://target.host/iNCIPit.cgi'
# (--insecure argument only necessary if you test a target.host lacking a valid cert) 
```

---
