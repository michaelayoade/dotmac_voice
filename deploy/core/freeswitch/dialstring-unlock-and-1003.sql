-- Create extension 1003 (3rd member for ring-group/queue tests)
INSERT INTO v_extensions (extension_uuid, domain_uuid, extension, password, user_context, accountcode, enabled, call_timeout)
VALUES (gen_random_uuid(), 'e05cc366-e8dd-4c04-a553-4f84096e1f55', '1003', 'Voice1003Test', 'voicetest.dotmac', 'voicetest.dotmac', 'true', 30)
ON CONFLICT DO NOTHING;

-- THE UNLOCK: route FusionPBX user/<ext> bridges to Kamailio (WS clients register there, not FS).
-- Affects only user/ bridges (ring group / IVR / queue); the direct 1xxx path doesn't use it.
UPDATE v_extensions
SET dial_string = '{sip_invite_domain=${domain_name},sip_h_X-Voice-Domain=${domain_name}}sofia/external/${dialed_user}@10.10.10.1:5060'
WHERE extension IN ('1001','1002','1003') AND domain_uuid='e05cc366-e8dd-4c04-a553-4f84096e1f55';

-- voicemail box for 1003 (no-answer fallback)
INSERT INTO v_voicemails (voicemail_uuid, domain_uuid, voicemail_id, voicemail_password, voicemail_enabled)
VALUES (gen_random_uuid(), 'e05cc366-e8dd-4c04-a553-4f84096e1f55', '1003', '1003', 'true')
ON CONFLICT DO NOTHING;

SELECT extension, left(dial_string, 55) AS dial_string FROM v_extensions WHERE extension IN ('1001','1002','1003') ORDER BY extension;
