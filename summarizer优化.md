从trace记录来看（用script/conv.py协助诊断）， 

convid

625fe418-503d-47de-a470-234e385b0508 第一条消息
da5c9ffa-7778-4a22-bc73-321c2e191977 第一条消息的summarize
6b51911b-2d68-4f52-a66b-3e870f4ab2bb 触发接续。但是trace中没有summarize的内容。summary应该放到user消息内所以也应该是内容。还是说有什么问题？

f70c2f7a-46d0-4b4d-b67a-6083fc275504 第二次summarize. 


## 期望

理论上来说summarize之后，下次接续时应该记录为一条单一的user消息（新消息跟在后面）。也许现在有双user消息的问题。

此外，向用户汇报的压缩说明只调整为一条（结束时），避免spam消息。

## By the way

我注意到bot在反反复复浪费web search的api额度，而且浪费了大量token. 怎么处理这个事情比较好呢