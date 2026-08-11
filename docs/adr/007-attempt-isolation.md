# ADR 007: Isolate attempt outputs

Status: accepted

Retries never reuse an output directory. The prefix contains run, job, attempt and fence, and Spark
uses error-if-exists writes. Publication references only the accepted manifest. This uses additional
temporary storage but prevents speculative or late attempts from corrupting accepted data.
