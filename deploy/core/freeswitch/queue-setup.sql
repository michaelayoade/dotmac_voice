-- Call-center queue 5000 (Support), ring-all, agents 1002 + 1003
INSERT INTO v_call_center_queues
 (call_center_queue_uuid, domain_uuid, queue_name, queue_extension, queue_strategy, queue_moh_sound,
  queue_time_base_score, queue_max_wait_time, queue_max_wait_time_with_no_agent,
  queue_max_wait_time_with_no_agent_time_reached, queue_tier_rules_apply, queue_tier_rule_wait_second,
  queue_tier_rule_no_agent_no_wait, queue_discard_abandoned_after, queue_abandoned_resume_allowed,
  queue_cc_exit_keys, queue_context)
VALUES
 ('11111111-0000-0000-0000-000000005000', 'e05cc366-e8dd-4c04-a553-4f84096e1f55', 'Support', '5000',
  'ring-all', '$${hold_music}', 'queue', 300, 30, 30, true, 30, true, 60, true, '*', 'voicetest.dotmac')
ON CONFLICT DO NOTHING;

-- Agents (callback type: dialed on demand via their contact -> Kamailio -> WS)
INSERT INTO v_call_center_agents
 (call_center_agent_uuid, domain_uuid, agent_name, agent_type, agent_call_timeout, agent_id,
  agent_contact, agent_status, agent_max_no_answer, agent_wrap_up_time, agent_reject_delay_time,
  agent_busy_delay_time)
VALUES
 ('22222222-0000-0000-0000-000000001002', 'e05cc366-e8dd-4c04-a553-4f84096e1f55', '1002', 'callback', 20,
  '1002', 'user/1002@voicetest.dotmac', 'Available (On Demand)', 3, 5, 2, 2),
 ('22222222-0000-0000-0000-000000001003', 'e05cc366-e8dd-4c04-a553-4f84096e1f55', '1003', 'callback', 20,
  '1003', 'user/1003@voicetest.dotmac', 'Available (On Demand)', 3, 5, 2, 2)
ON CONFLICT DO NOTHING;

-- Tiers: assign both agents to the queue (level 1)
INSERT INTO v_call_center_tiers
 (call_center_tier_uuid, domain_uuid, call_center_queue_uuid, call_center_agent_uuid, agent_name, queue_name, tier_level, tier_position)
VALUES
 (gen_random_uuid(), 'e05cc366-e8dd-4c04-a553-4f84096e1f55', '11111111-0000-0000-0000-000000005000', '22222222-0000-0000-0000-000000001002', '1002', 'Support', 1, 1),
 (gen_random_uuid(), 'e05cc366-e8dd-4c04-a553-4f84096e1f55', '11111111-0000-0000-0000-000000005000', '22222222-0000-0000-0000-000000001003', '1003', 'Support', 1, 1)
ON CONFLICT DO NOTHING;
