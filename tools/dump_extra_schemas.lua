-- Dump schemas that GET /v1/schema does not return. Run with `resty` inside the APISIX image.
package.cpath = "/usr/local/apisix/deps/lib/lua/5.1/?.so;/usr/local/apisix/deps/lib64/lua/5.1/?.so;"
    .. package.cpath
require("apisix.core.profile").apisix_home = "/usr/local/apisix/"
local cjson = require("cjson.safe")
cjson.encode_empty_table_as_object(true)
local schema_def = require("apisix.schema_def")
local constants = require("apisix.constants")

local out = { credential = schema_def.credential, secrets = {}, resources = {} }
for _, name in ipairs({ "vault", "aws", "gcp" }) do
    out.secrets[name] = require("apisix.secret." .. name).schema
end
for dir in pairs(constants.HTTP_ETCD_DIRECTORY) do out.resources[#out.resources + 1] = dir:sub(2) end
for dir in pairs(constants.STREAM_ETCD_DIRECTORY) do out.resources[#out.resources + 1] = dir:sub(2) end
table.sort(out.resources)
print(cjson.encode(out))
