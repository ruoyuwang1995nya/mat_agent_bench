Mat-bench question: {QUESTION_ID}
Server: {SERVER_URL}
Token: {TOKEN}
Session: {SESSION}

Steps:
1. Fetch question details: `curl {SERVER_URL}/questions/{QUESTION_ID}`
2. Download each data file listed in `response.data_files` into `/tmp/mat_bench/{SESSION}/{QUESTION_ID}/`:
   `curl {SERVER_URL}/questions/{QUESTION_ID}/data/{filename} -o /tmp/mat_bench/{SESSION}/{QUESTION_ID}/{filename}`
3. Complete the task described in `response.prompt`, working in `/tmp/mat_bench/{SESSION}/{QUESTION_ID}/`
4. Submit all output files and your final answer:
   `curl -X POST "{SERVER_URL}/submit/{QUESTION_ID}?session_id={SESSION}" \
     -H "X-API-Token: {TOKEN}" \
     -F 'meta={"answer":"<final answer>","model_name":"<model>","num_turns":<N>,"usage":{"prompt_tokens":<N>,"completion_tokens":<N>,"total_tokens":<N>},"tool_calls":[{"step":1,"tool_name":"bash","args":{"command":"..."},"observation_excerpt":"...","succeeded":true}]}' \
     -F 'file1=@/tmp/mat_bench/{SESSION}/{QUESTION_ID}/output_file.ext'`
5. Check your score:
   `curl -s "{SERVER_URL}/results/{QUESTION_ID}?session_id={SESSION}" -H "X-API-Token: {TOKEN}" | jq '.[0] | {run_status, passed_count, total_count, overall_weighted_score}'`
