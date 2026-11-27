using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.UI.Xaml.Controls;
using RailGo.Core.Models.Settings;
using RailGo.Core.Query.Online;

namespace RailGo.Views.Pages.Settings;

public sealed partial class AcknowledgementsPage : Page
{
    private const string QqAvatarTemplate = "https://q1.qlogo.cn/g?b=qq&s=100&nk={0}";

    // 前端硬编码的开发成员（姓名 + QQ 号）
    private static readonly (string Name, string Qq)[] Developers =
    {
        ("辰墨", "1316280182"),
        ("TKP30", "572747232"),
        ("姜胤", "1219439938"),
        ("mstouk57g", "3776473661"),
        ("心不负相思", "1808717406")
    };

    // 前端硬编码的特别鸣谢成员（姓名 + QQ 号）
    private static readonly (string Name, string Qq)[] SpecialThanks =
    {
        ("XieXiLin", "3068342155"),
        ("bcdt", "2396807944"),
        ("lihugang", "3186095880"),
        ("Sutarn", "1482163108"),
        ("小白告天", "11482827")
    };

    // 前端硬编码的服务器赞助者（仅 QQ 号）
    private static readonly string[] ServerSponsors =
    {
        "1708208890", "525637945", "11482827", "740528989",
        "375166221", "1067519395", "2068364343", "3418736198"
    };

    // 本地硬编码的动车组图片贡献者（仅 QQ 号）
    private static readonly string[] LocalPicContributors =
    {
        "1972867189", "2151235831", "1069943593", "525637945", "3596323062",
        "2377807972", "982588465", "1779378782", "516127399", "2533221373",
        "2387845184", "365736548", "1983163227", "3111737636"
    };

    private static readonly DataSourceCredit[] DataSources =
    {
        new() { Name = "RailGo.Parser", Website = "railgo.dev", Features = "车次、车站、正晚点、动车组担当、大屏" },
        new() { Name = "OpenCRHTracker", Website = "crh.lihugang.top", Features = "担当、配属" },
        new() { Name = "CT Photos", Website = "train.idcmoss.cn", Features = "列车图鉴" }
    };

    public AcknowledgementsPage()
    {
        InitializeComponent();

        DevelopersRepeater.ItemsSource = Developers
            .Select(person => new CreditPerson { Name = person.Name, Avatar = GetAvatarUrl(person.Qq) })
            .ToList();
        SpecialThanksRepeater.ItemsSource = SpecialThanks
            .Select(person => new CreditPerson { Name = person.Name, Avatar = GetAvatarUrl(person.Qq) })
            .ToList();
        ServerSponsorRepeater.ItemsSource = ToAvatars(ServerSponsors);
        PicContributorsRepeater.ItemsSource = ToAvatars(LocalPicContributors);
        DataSourcesList.ItemsSource = DataSources;

        Loaded += OnPageLoaded;
    }

    private async void OnPageLoaded(object sender, Microsoft.UI.Xaml.RoutedEventArgs e)
    {
        Loaded -= OnPageLoaded;
        await MergeRemotePicContributorsAsync();
    }

    /// <summary>
    /// 拉取远端图片贡献者名单并与本地名单合并去重，失败时保留本地名单
    /// </summary>
    private async Task MergeRemotePicContributorsAsync()
    {
        try
        {
            var remoteContributors = await SettingsAPIService.GetEmuImageContributorsAsync();
            if (remoteContributors is not { Count: > 0 })
            {
                return;
            }

            var merged = new List<string>(LocalPicContributors);
            foreach (var qq in remoteContributors)
            {
                var account = qq?.Trim();
                if (!string.IsNullOrEmpty(account) && !merged.Contains(account))
                {
                    merged.Add(account);
                }
            }

            PicContributorsRepeater.ItemsSource = ToAvatars(merged);
        }
        catch
        {
            // 远程名单获取失败时保留本地硬编码名单
        }
    }

    private static List<CreditPerson> ToAvatars(IEnumerable<string> qqAccounts)
    {
        return qqAccounts
            .Select(qq => new CreditPerson { Avatar = GetAvatarUrl(qq) })
            .ToList();
    }

    private static string GetAvatarUrl(string qq)
    {
        return string.Format(QqAvatarTemplate, qq);
    }
}
