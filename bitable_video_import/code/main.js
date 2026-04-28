const axios = require("axios");
const fs = require("fs");
const path = require("path");

// === input params start
const appID = process.env.APP_ID; // app_id, required, 应用 ID
// 应用唯一标识，创建应用后获得。有关app_id 的详细介绍。请参考通用参数https://open.feishu.cn/document/ukTMukTMukTM/uYTM5UjL2ETO14iNxkTN/terminology。
const appSecret = process.env.APP_SECRET; // app_secret, required, 应用 secret
// 应用秘钥，创建应用后获得。有关 app_secret 的详细介绍，请参考https://open.feishu.cn/document/ukTMukTMukTM/uYTM5UjL2ETO14iNxkTN/terminology。
const baseUrl = process.env.BASE_URL; // string, required, 多维表格 URL
// 多维表格 App 的唯一标识。不同形态的多维表格，其 app_token 的获取方式不同：- 如果多维表格的 URL 以 ==**feishu.cn/base**== 开头，该多维表格的 app_token 是下图高亮部分：![app_token.png](//sf3-cn.feishucdn.com/obj/open-platform-opendoc/6916f8cfac4045ba6585b90e3afdfb0a_GxbfkJHZBa.png?height=766&lazyload=true&width=3004)- 如果多维表格的 URL 以 ==**feishu.cn/wiki**== 开头，你需调用知识库相关[获取知识空间节点信息](https://go.feishu.cn/s/65W4PEw1g04)接口获取多维表格的 app_token。当 obj_type 的值为 bitable 时，obj_token 字段的值才是多维表格的 app_token。了解更多，参考[多维表格 app_token 获取方式](https://go.feishu.cn/s/671HilYws03#-752212c)。
const userAccessToken = process.env.USER_ACCESS_TOKEN; // uat, required, 用户访问凭证
// 通过用户授权获取user_access_token，用于访问多维表格
const downloadPath = process.env.DOWNLOAD_PATH; // string, required, 下载文件夹路径
// 本地文件夹路径，用于保存下载的视频附件
// === input params end

// 把错误信息和排查建议打印出来，方便排查
function axiosErrorLog(response) {
  const data = response?.data;
  if (data?.error) {
    console.error("Error:", data);
  }
}

// 获取 tenant_access_token
async function getTenantAccessToken(appID, appSecret) {
  const url =
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal";

  const payload = {
    app_id: appID,
    app_secret: appSecret,
  };

  try {
    const response = await axios.post(url, payload, {
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });

    const result = response.data;
    if (result.code !== 0) {
      console.error("Error:", result);
      throw new Error(`failed to get tenant_access_token: ${result.msg}`);
    }
    return result.tenant_access_token;
  } catch (error) {
    axiosErrorLog(error.response);
    throw new Error(`Error getting tenant_access_token: ${error.message}`);
  }
}

/**
 * 获取知识空间节点信息
 * @param {string} tenantAccessToken
 * @param {string} nodeToken
 * @returns {Promise<object>} 返回 node 对象
 */
async function getWikiNodeInfo(tenantAccessToken, nodeToken) {
  // 文档：https://open.feishu.cn/document/ukTMukTMukTM/uUDN04SN0QjL1QDN/wiki-v2/space/get_node
  const url = `https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node?token=${encodeURIComponent(
    nodeToken
  )}`;
  const headers = {
    Authorization: `Bearer ${tenantAccessToken}`,
    "Content-Type": "application/json; charset=utf-8",
  };

  try {
    console.log("GET:", url);
    const response = await axios.get(url, { headers });
    const result = response.data;
    if (result.code !== 0) {
      console.error("ERROR: 获取知识空间节点信息失败", result);
      throw new Error(`failed to get wiki node info: ${result.msg}`);
    }
    if (!result.data || !result.data.node) {
      throw new Error("未获取到节点信息");
    }
    console.log("节点信息获取成功:", {
      node_token: result.data.node.node_token,
      obj_type: result.data.node.obj_type,
      obj_token: result.data.node.obj_token,
      title: result.data.node.title,
    });
    return result.data.node;
  } catch (error) {
    axiosErrorLog(error.response);
    throw new Error(`Error getting wiki node info: ${error.message}`);
  }
}

// 解析多维表格参数
async function parseBaseUrl(tenantAccessToken, baseUrlString) {
  const baseUrl = new URL(baseUrlString);
  const pathname = baseUrl.pathname;
  let appToken = baseUrl.pathname.split("/").at(-1);

  if (pathname.includes("/wiki/")) {
    const nodeInfo = await getWikiNodeInfo(tenantAccessToken, appToken);
    appToken = nodeInfo.obj_token;
  }

  const viewID = baseUrl.searchParams.get("view");
  const tableID = baseUrl.searchParams.get("table");
  return { appToken, tableID, viewID };
}

// 获取数据表ID列表
async function listTables(tenantAccessToken, appToken) {
  const url = `https://open.feishu.cn/open-apis/bitable/v1/apps/${appToken}/tables`;
  const headers = {
    Authorization: `Bearer ${tenantAccessToken}`,
    "Content-Type": "application/json; charset=utf-8",
  };

  try {
    console.log("GET:", url);
    const response = await axios.get(url, { headers });
    const result = response.data;

    if (result.code !== 0) {
      console.error("Error:", result);
      throw new Error(`failed to list tables: ${result.msg}`);
    }

    console.log("获取数据表列表成功，数量:", result.data.items.length);
    return result.data.items;
  } catch (error) {
    axiosErrorLog(error.response);
    throw new Error(`Error listing tables: ${error.message}`);
  }
}

// 获取字段列表
async function listFields(tenantAccessToken, appToken, tableID) {
  let hasMore = true;
  let pageToken = "";
  let fields = [];

  while (hasMore) {
    const url = `https://open.feishu.cn/open-apis/bitable/v1/apps/${appToken}/tables/${tableID}/fields?page_size=100${pageToken ? `&page_token=${encodeURIComponent(pageToken)}` : ""}`;
    const headers = {
      Authorization: `Bearer ${tenantAccessToken}`,
      "Content-Type": "application/json; charset=utf-8",
    };

    try {
      console.log("GET:", url);
      const response = await axios.get(url, { headers });
      const result = response.data;

      if (result.code !== 0) {
        console.error("Error:", result);
        throw new Error(`failed to list fields: ${result.msg}`);
      }

      console.log("获取字段列表成功，数量:", result.data.items.length);
      fields = fields.concat(result.data.items);
      hasMore = result.data.has_more;
      pageToken = result.data.page_token;
    } catch (error) {
      axiosErrorLog(error.response);
      throw new Error(`Error listing fields: ${error.message}`);
    }
  }

  return fields;
}

// 获取所有记录
async function getRecords(userAccessToken, appToken, tableID) {
  let hasMore = true;
  let pageToken = "";
  let records = [];

  while (hasMore) {
    const url = `https://open.feishu.cn/open-apis/bitable/v1/apps/${appToken}/tables/${tableID}/records/search?page_size=500${pageToken ? `&page_token=${encodeURIComponent(pageToken)}` : ""}`;
    const headers = {
      Authorization: `Bearer ${userAccessToken}`,
      "Content-Type": "application/json; charset=utf-8",
    };

    const payload = {};

    try {
      console.log("POST:", url);
      const response = await axios.post(url, payload, { headers });
      const result = response.data;

      if (result.code !== 0) {
        console.error("Error:", result);
        throw new Error(`failed to get records: ${result.msg}`);
      }

      console.log("获取记录成功，数量:", result.data.items.length);
      records = records.concat(result.data.items);
      hasMore = result.data.has_more;
      pageToken = result.data.page_token;
    } catch (error) {
      axiosErrorLog(error.response);
      throw new Error(`Error getting records: ${error.message}`);
    }
  }

  console.log("所有记录获取完成，总数:", records.length);
  return records;
}

// 下载视频附件
async function downloadVideoAttachment(userAccessToken, fileToken, extra, fileName, downloadPath) {
  // 确保下载目录存在
  if (!fs.existsSync(downloadPath)) {
    fs.mkdirSync(downloadPath, { recursive: true });
  }

  const url = `https://open.feishu.cn/open-apis/drive/v1/medias/${fileToken}/download${extra ? `?extra=${encodeURIComponent(extra)}` : ""}`;
  const headers = {
    Authorization: `Bearer ${userAccessToken}`,
  };

  try {
    console.log("GET:", url);
    const response = await axios.get(url, {
      headers,
      responseType: "stream",
    });

    const filePath = path.join(downloadPath, fileName);
    const writer = fs.createWriteStream(filePath);

    return new Promise((resolve, reject) => {
      response.data.pipe(writer);

      writer.on("finish", () => {
        console.log(`视频附件下载成功: ${fileName}`);
        resolve(filePath);
      });

      writer.on("error", (err) => {
        console.error(`下载视频附件失败: ${fileName}`, err);
        reject(err);
      });
    });
  } catch (error) {
    axiosErrorLog(error.response);
    throw new Error(`Error downloading video attachment: ${error.message}`);
  }
}

// 主函数：下载指定多维表格中附件字段的视频附件
async function downloadVideoAttachmentsFromBitable() {
  try {
    // 获取 tenant_access_token
    const tenantAccessToken = await getTenantAccessToken(appID, appSecret);
    
    // 解析多维表格参数
    const { appToken, tableID, viewID } = await parseBaseUrl(tenantAccessToken, baseUrl);
    
    // 如果没有提供 tableID，则获取第一个数据表
    let targetTableID = tableID;
    if (!targetTableID) {
      const tables = await listTables(tenantAccessToken, appToken);
      if (tables.length === 0) {
        throw new Error("多维表格中没有数据表");
      }
      targetTableID = tables[0].table_id;
      console.log("未指定数据表，使用第一个数据表:", targetTableID);
    }
    
    // 获取字段列表，找到附件字段
    const fields = await listFields(tenantAccessToken, appToken, targetTableID);
    const attachmentFields = fields.filter(field => field.type === 17); // 17 表示附件字段
    
    if (attachmentFields.length === 0) {
      console.log("未找到附件字段");
      return;
    }
    
    console.log("找到附件字段数量:", attachmentFields.length);
    attachmentFields.forEach(field => {
      console.log(`附件字段: ${field.field_name} (field_id: ${field.field_id})`);
    });
    
    // 获取所有记录
    const records = await getRecords(userAccessToken, appToken, targetTableID);
    
    if (records.length === 0) {
      console.log("没有记录需要处理");
      return;
    }
    
    // 遍历记录，下载视频附件
    let downloadCount = 0;
    for (const record of records) {
      for (const field of attachmentFields) {
        const fieldName = field.field_name;
        const fieldValue = record.fields[fieldName];
        
        if (!fieldValue || !Array.isArray(fieldValue)) {
          continue;
        }
        
        for (const attachment of fieldValue) {
          // 检查是否为视频文件（根据 MIME 类型）
          if (attachment.mime_type && attachment.mime_type.startsWith("video/")) {
            const fileToken = attachment.file_token;
            const fileName = attachment.name || `${fileToken}.${attachment.mime_type.split("/")[1]}`;
            const extra = attachment.extra;
            
            try {
              await downloadVideoAttachment(userAccessToken, fileToken, extra, fileName, downloadPath);
              downloadCount++;
            } catch (error) {
              console.error(`下载视频附件失败: ${fileName}`, error.message);
            }
          }
        }
      }
    }
    
    console.log(`视频附件下载完成，共下载 ${downloadCount} 个文件`);
  } catch (error) {
    console.error("执行过程中发生错误:", error.message);
    throw error;
  }
}

async function main() {
  try {
    await downloadVideoAttachmentsFromBitable();
  } catch (error) {
    console.error("程序执行失败:", error.message);
    process.exit(1);
  }
}

main();