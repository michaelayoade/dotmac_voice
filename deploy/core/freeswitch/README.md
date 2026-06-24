# FreeSWITCH/FusionPBX side of FS-in-path (DB-managed)

FusionPBX renders FreeSWITCH config via mod_xml_curl and **caches** it under
`/var/cache/fusionpbx/`. After any DB change, flush the relevant cache file and reload, e.g.:
- ACL: `rm /var/cache/fusionpbx/configuration.acl.conf && fs_cli -x reloadacl`
- Dialplan: `rm /var/cache/fusionpbx/dialplan.* && fs_cli -x reloadxml`

## Trust Kamailio (Task 1) — DONE
Kamailio (`10.10.10.1`) added to the **`providers`** access control (uuid
`aacb42b3-eb5f-4cb7-b6e8-03ea9306c05a`) as `allow 10.10.10.1/32`. The internal profile has
`apply-inbound-acl=providers` + `auth-calls=true`, so providers-matched IPs are trusted trunk
peers and bypass digest auth. Verified: `fs_cli -x 'acl 10.10.10.1 providers'` => true.

SQL (idempotent record; survives a future UI regen):
```sql
INSERT INTO v_access_control_nodes (access_control_node_uuid, access_control_uuid, node_type, node_cidr, node_description, insert_date)
VALUES (gen_random_uuid(), 'aacb42b3-eb5f-4cb7-b6e8-03ea9306c05a', 'allow', '10.10.10.1/32', 'Kamailio WebRTC edge (FS-in-path)', now());
```

## Gateway — NOT used
FS reaches Kamailio via a direct SIP URI bridge (`sofia/external/$dest@10.10.10.1:5060`); no
FusionPBX gateway object needed.
