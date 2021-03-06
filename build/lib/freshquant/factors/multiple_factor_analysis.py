# coding: utf8
import pandas as pd
import numpy as np
import os

from .analysis import add_group
from .data_type import FactorData
from .metrics import information_coefficient
from .util import __frame_to_rank,form_fenceng,fenceng_score,drop_middle
from .config import FACTORS_DETAIL_PATH
import statsmodels.formula.api as smf

def regress(fac_ret_data,method='common_regress'):
    # 先按照股票代码排序
    fac_ret_data = fac_ret_data.sort_index()
    # 因子名称
    factor_names = set(fac_ret_data.columns) - {'ret', 'cap', 'benchmark_returns', 'group'}
    # 非因子列名称
    other_names = sorted(set(fac_ret_data.columns) - factor_names)
    # 判断因子长度是否大于等于1
    if len(factor_names) >= 1:
        factor_names = sorted(factor_names)
    else:
        raise ValueError('fac_ret_data must have at least 1 factor, got 0.')

    def ret_ols_fac(x):
        x=x.reset_index(level=0,drop=True)
        formula='ret~'+('+'.join(factor_names))
        resid=pd.DataFrame(smf.ols(formula=formula,data=x).fit().resid,columns=['resid'])
        params=smf.ols(formula=formula,data=x).fit().params
        for inx in params.index:
            if inx == 'Intercept':
                resid[inx] = params[inx]
            else:
                resid['beta_'+inx]=params[inx]
        return resid
    def get_params():
        """
        回归法：每个dt,股票收益率序列对各因子值序列进行回归，得到回归系数和残差
        Returns:
            DataFrame: 各因子各期的回归系数
        """
        params=fac_ret_data[factor_names+['ret']].groupby(level=0).apply(lambda x:ret_ols_fac(x))
        return params

    def common_regress():
        params=get_params()
        # 回归参数错位一期
        params=params.groupby(level=1).shift(1).dropna()
        combined=pd.concat([fac_ret_data[factor_names],params],axis=1).dropna()
        combined['ret']=combined['Intercept']+combined['resid']
        for fac in factor_names:
            combined['ret']+=combined[fac]*combined['beta_'+fac]
        return pd.concat([combined[['ret']],fac_ret_data],axis=1,join_axes=[combined.index])
    valid_method = {'common_regress': common_regress}
    try:
        return valid_method[method]()
    except KeyError:
        print(('{} is not a valid method. valid methods are: {}'.format(
            method, list(valid_method.keys()))))
        raise

def score(fac_ret_data, method='equal_weighted', direction=None,score_window=12,num_group=5):
    """
    给多因子打分
    Args:
        fac_ret_data (DataFrame): multi_indexed 数据框, 有若干因子列, ret, cap, benchmark_returns等.
        method(str): 打分方法.
                    {'equal_weighted', 'ic_weighted', 'icir_weighted', 'ret_weighted', 'ret_icir_weighted'}
                    * equal_weighted: 等权法
                    * ic_weighted: ic 加权
                    * icir_weighted: icir 加权
                    * ret_weighted: 收益率加权, 取第一个分组的平均收益率为该因子收益率.
                    * ret_icir_weighted: 因子icir加权

        direction (bool or dict): direction 表示因子方向, False:代表因子值降序(因子值越大得分越高),True:代表因子值升序(因子值越小得分越高)
                                 默认排序, 可以是一个bool值, 所有因子相同排序, 也可以是一个字典,其key是因子名称, value是bool值.
        rank_method (str) : {'average', 'min', 'max', 'first', 'dense'}
                            * average: average rank of group
                            * min: lowest rank in group
                            * max: highest rank in group
                            * first: ranks assigned in order they appear in the array
                            * dense: like 'min', but rank always increases by 1 between groups
        na_option (str): {'keep', 'top', 'bottom'}
                            * keep: leave NA values where they are
                            * top: smallest rank if ascending
                            * bottom: smallest rank if descending
        score_window (int): 滑动窗口大小, 对equal_weighted无效
        num_group(int): 分组数, 该参数仅对method='ret_weighted' 有效
        group_ascending(bool or dict): 分组时的排序, 默认值False,所有因子分组时按降序排序.
                                       也可指定具体某一个或多个因子为升序,其他默认降序.
                                       该参数仅对method='ret_weighted' 有效
    Returns:
        DataFrame, 打完分的DataFrame,
    Raises:
        KeyError, 如果输入的打分方法有误,会引发该错误.
    """
    # 先按照股票代码排序
    fac_ret_data = fac_ret_data.sort_index()
    # 因子名称
    factor_names = set(fac_ret_data.columns) - {'ret', 'cap', 'benchmark_returns', 'group'}
    # 非因子列名称
    other_names = sorted(set(fac_ret_data.columns) - factor_names)
    # 判断因子长度是否大于等于1
    if len(factor_names) >= 1:
        factor_names = sorted(factor_names)
    else:
        raise ValueError('fac_ret_data must have at least 1 factor, got 0.')

    def get_ic():
        """
        计算IC序列
        """
        ic = fac_ret_data.groupby(level=0).apply(
            lambda frame: [information_coefficient(frame[fac], frame['ret'])[0] for fac in factor_names])
        # 上一步骤得到的结果是一个序列,序列元素是一列表,需要将其拆开
        ic = pd.DataFrame(ic.tolist(), index=ic.index, columns=factor_names)
        return ic

    def get_rank():
        """
        因子方向可以指定，如果没有指定，则按照默认定义的方向（通过因子文件获取）
        取得因子排名. 规则如下: direction 默认为False, 也就是值越大越好,得分越高,会被分入靠前的组.
        对于NA的处理,是放到最后,强制使其rank为0
        Args:

        Returns:
            DataFrame: 因子rank
        """
        if direction is None:
            fac_info = pd.read_csv(FACTORS_DETAIL_PATH,encoding='GBK')
            default_direction = dict(fac_info[['code', 'ascend']][fac_info['code'].isin(factor_names)].values)
            rnk_list = [fac_ret_data[col].groupby(level=0).rank(ascending=not default_direction[col],
                                                                na_option='keep',method='first') for col in factor_names]
            rnk = pd.concat(rnk_list, axis=1)
        elif isinstance(direction, bool):
            rnk = fac_ret_data[factor_names].groupby(level=0).rank(ascending=not direction, na_option='keep',
                                                                   method='first')
        elif isinstance(direction, dict):
            default_direction = dict(
                list(zip(factor_names, [False] * len(factor_names))))
            if len(direction) != len(factor_names):
                print('direction 长度与因子数目不同, 未指明的将按照默认降序排序(大值排名靠前).')
            default_direction.update(direction)
            rnk_list = [fac_ret_data[col].groupby(level=0).rank(ascending=not default_direction[col], na_option='keep',
                                                                method='first')
                        for col in factor_names]
            rnk = pd.concat(rnk_list, axis=1)
        # 假设有k个NA, 未执行下句时, rank 值 从1..(N-k), 执行后, rnk值是从k+1..N
        rnk = rnk.groupby(level=0).apply(lambda x:x+x.isnull().sum())
        # fillna后, NA的rank被置为0.
        rnk = rnk.fillna(0.0)
        return rnk

    def context_weighted():
        """
        因子打分:市值情景加权(由于市值因子分层显著,此处默认以市值因子为分层因子)
        1. 根据因子方向调整因子值
        2. 根据ic计算各情景因子权重矩阵..
        3. 对于不同个股，按照披露值进行打分，并得到个股情景权重
        """
        if isinstance(direction, bool):
            default_direction = dict(
                list(zip(factor_names, [False] * len(factor_names))))
        elif isinstance(direction, dict):
            default_direction = dict(
                list(zip(factor_names, [False] * len(factor_names))))
            if len(direction) != len(factor_names):
                print('direction 长度与因子数目不同, 未指明的将按照默认降序排序(大值排名靠前).')
            default_direction.update(direction)
        factors=factor_names + ['cap']
        # 1. 原始因子值和市值数据
        fac_ret_cap=fac_ret_data[factors]
        # 2. 根据因子方向调整因子值
        fac_ret_cap = __frame_to_rank(default_direction, fac_ret_cap)
        df1 = fac_ret_cap[factor_names]
        # 3. 根据ic计算各情景因子权重矩阵
        df2 = form_fenceng('M004023', factor_names)
        # 4. 对于每个dt  各分层因子上的暴露值进行打分
        fac_ret_cap2 = fac_ret_cap['cap']
        df3 = fenceng_score(fac_ret_cap2)
        # 计算df1和df2 乘积
        df_two = [a[0].dot(a[1].values) for a in zip(
            list(dict(list(df1.groupby(level=0))).values()),
            list(dict(list(df2.groupby(level=0))).values()))]
        df_two2 = pd.concat(df_two).sort_index(level=0)
        # 计算df1 df2 df3 乘积
        df_three = [a[0].dot(a[1].loc[:, list(a[0].index.get_level_values(level=1))].values) for a in zip(
            list(dict(list(df_two2.groupby(level=0))).values()),
            list(dict(list(df3.groupby(level=0))).values()))]
        # 取对角线值
        for i in range(len(df_three)):
            df_three[i]['score'] = np.diag(df_three[i].values)
        score = pd.concat([df_three[i]['score'] for i in range(len(df_three))])
        score_ = pd.DataFrame(score).sort_index()
        return score_.join(fac_ret_data)

    def equal_weighted():
        """
        因子打分:等权法
        Returns:
            DataFrame: 一个数据框,有score列,和ret, cap,等列
        """
        rnk = get_rank()
        weight = pd.DataFrame(1, index=fac_ret_data[factor_names].index.levels[0],columns=fac_ret_data[factor_names].columns)
        score_ = (rnk * weight).mean(axis=1).to_frame().rename(columns={0: 'score'})
        return score_.join(fac_ret_data).sort_index()

    def ic_weighted():
        """
        因子打分: IC加权法.
        Returns:
            DataFrame: 一个数据框,有score列,和ret, cap,等列
        Notes:
            IC 取的是绝对值.
        """
        ic = get_ic().abs()
        rolling_ic = ic.rolling(score_window, min_periods=1).mean()
        weight = rolling_ic.div(rolling_ic.sum(axis=1),axis='index')

        rank = get_rank()
        score_ = (rank * weight).sum(axis=1).to_frame().rename(columns={0: 'score'})

        return score_.join(fac_ret_data).sort_index()

    def icir_weighted():
        """
        因子打分:ic-ir加权法
        Returns:
            DataFrame: 一个数据框,有score列,和ret, cap,等列
        Notes:
            ic用的是绝对值.
            对于第1期, 权重采用的的是第1期的ic
            默认的min_periods采用1, 也就是计算滑动均值和方差时, 对于小于滑动窗口的前几期, 采用的是实际长度,而不是填充NA
        """
        ic = get_ic().abs()
        rolling_ic = ic.rolling(score_window, min_periods=1).mean()
        rolling_std = ic.rolling(score_window, min_periods=1).std()
        rolling_std.iloc[0,:]=1  # rolling_std 第一行是NAN

        icir = rolling_ic / rolling_std
        weight = icir.div(icir.sum(axis=1),axis='index')

        rank = get_rank()
        score_ = (rank * weight).sum(axis=1).to_frame().rename(columns={0: 'score'})
        return score_.join(fac_ret_data).sort_index()

    def ret_weighted():
        first_group_returns = []
        rank = get_rank()
        for factor_name in factor_names:
            data=rank[[factor_name]].rename(columns={factor_name:'score'}).join(fac_ret_data[other_names])
            data=add_group(data,num_group=5)
            # 取得每个因子第一个分组的收益率
            ret = data.groupby([data.index.get_level_values(0), data.group])['ret'].mean().unstack()[['Q01']].rename(
                columns={'Q01': factor_name})
            first_group_returns.append(ret)
        returns = pd.concat(first_group_returns,axis=1)
        returns = returns.rolling(score_window, min_periods=1).mean()

        weight = returns.div(returns.sum(axis=1),axis='index')
        score_ = (rank * weight).sum(axis=1).to_frame().rename(columns={0: 'score'})
        return score_.join(fac_ret_data).sort_index()

    valid_method = {'equal_weighted': equal_weighted,
                    'ic_weighted': ic_weighted,
                    'icir_weighted': icir_weighted,
                    'ret_weighted': ret_weighted,
                    'context_weighted':context_weighted}
    try:
        return valid_method[method]()
    except KeyError:
        print(('{} is not a valid method. valid methods are: {}'.format(
            method, list(valid_method.keys()))))
        raise


def dynamic_score(fac_ret_data, method='equal_weighted', direction=False, rank_method='first', na_option='keep',
                  score_window=12,
                  num_group=5, group_ascending=False):
    """
    动态打分
    """


    factor_names = set(fac_ret_data.columns) - \
                   {'ret', 'cap', 'benchmark_returns', 'group'}
    # 非因子列名称
    other_names = sorted(set(fac_ret_data.columns) - factor_names)
    if len(factor_names) >= 1:
        factor_names = sorted(factor_names)
    else:
        raise ValueError('fac_ret_data must have at least 1 factor, got 0.')


    def get_ic():
        ic = fac_ret_data.groupby(level=0).apply(
            lambda frame: [information_coefficient(frame[fac], frame['ret'])[0] for fac in factor_names])
        # 上一步骤得到的结果是一个序列,序列元素是一列表,需要将其拆开
        ic = pd.DataFrame(ic.tolist(), index=ic.index, columns=factor_names)
        return ic


    def get_rank():
        """
        取得因子排名. 规则如下: direction 默认为False, 也就是值越大越好,得分越高,会被分入靠前的组.
        对于NA的处理,是放到最后,强制使其rank为0
        Args:

        Returns:
            DataFrame: 因子rank
        """
        if isinstance(direction, bool):
            rnk = fac_ret_data[factor_names].groupby(level=0).rank(ascending=not direction, na_option=na_option,
                                                                   method=rank_method)
        elif isinstance(direction, dict):
            default_direction = dict(
                list(zip(factor_names, [False] * len(factor_names))))
            if len(direction) != len(factor_names):
                print('direction 长度与因子数目不同, 未指明的将按照默认降序排序(大值排名靠前).')
            default_direction.update(direction)
            rnk_list = [fac_ret_data[col].groupby(level=0).rank(ascending=not default_direction[col], na_option=na_option,
                                                                method=rank_method)
                        for col in factor_names]
            rnk = pd.concat(rnk_list, axis=1)
        # 假设有k个NA, 未执行下句时, rank 值 从1..(N-k), 执行后, rnk值是从k+1..N
        rnk += rnk.isnull().sum()
        # fillna后, NA的rank被置为0.
        rnk = rnk.fillna(0.0)
        return rnk


    def context_weighted():
        """
        因子打分:市值情景加权(由于市值因子分层显著,此处默认以市值因子为分层因子)
        1. 根据因子方向调整因子值
        2. 根据ic计算各情景因子权重矩阵..
        3. 对于不同个股，按照披露值进行打分，并得到个股情景权重
        """
        if isinstance(direction, bool):
            default_direction = dict(
                list(zip(factor_names, [False] * len(factor_names))))
        elif isinstance(direction, dict):
            default_direction = dict(
                list(zip(factor_names, [False] * len(factor_names))))
            if len(direction) != len(factor_names):
                print('direction 长度与因子数目不同, 未指明的将按照默认降序排序(大值排名靠前).')
            default_direction.update(direction)
        factors = factor_names + ['cap']
        # 1. 原始因子值和市值数据
        fac_ret_cap = fac_ret_data[factors]
        # 2. 根据因子方向调整因子值
        fac_ret_cap = __frame_to_rank(default_direction, fac_ret_cap)
        df1 = fac_ret_cap[factor_names]
        # 3. 根据ic计算各情景因子权重矩阵
        df2 = form_fenceng('M004023', factor_names)
        # 4. 对于每个dt  各分层因子上的暴露值进行打分
        fac_ret_cap2 = fac_ret_cap['cap']
        df3 = fenceng_score(fac_ret_cap2)
        # 计算df1和df2 乘积
        df_two = [a[0].dot(a[1].values) for a in zip(
            list(dict(list(df1.groupby(level=0))).values()),
            list(dict(list(df2.groupby(level=0))).values()))]
        df_two2 = pd.concat(df_two).sort_index(level=0)
        # 计算df1 df2 df3 乘积
        df_three = [a[0].dot(a[1].loc[:, list(a[0].index.get_level_values(level=1))].values) for a in zip(
            list(dict(list(df_two2.groupby(level=0))).values()),
            list(dict(list(df3.groupby(level=0))).values()))]
        # 取对角线值
        for i in range(len(df_three)):
            df_three[i]['score'] = np.diag(df_three[i].values)
        score = pd.concat([df_three[i]['score'] for i in range(len(df_three))])
        score_ = pd.DataFrame(score).sort_index()
        return score_.join(fac_ret_data)


    def equal_weighted():
        """
        因子打分:等权法
        Returns:
            DataFrame: 一个数据框,有score列,和ret, cap,等列
        """
        rnk = get_rank()
        score_ = fac_ret_data[factor_names].mul(rnk.mean(axis=1), axis=0).sum(axis=1).to_frame().rename(
            columns={0: 'score'})

        return score_.join(fac_ret_data)


    def ic_weighted():
        """
        因子打分: IC加权法.
        Returns:
            DataFrame: 一个数据框,有score列,和ret, cap,等列
        Notes:
            IC 取的是绝对值.
        """
        ic = get_ic().abs()
        rolling_ic = ic.rolling(score_window, min_periods=1).mean()
        weight = rolling_ic / rolling_ic.sum(axis=1)

        rank = get_rank()
        score_ = (
            rank * weight).sum(axis=1).to_frame().rename(columns={0: 'score'})

        return score_.join(fac_ret_data)


    def icir_weighted():
        """
        因子打分:ic-ir加权法
        Returns:
            DataFrame: 一个数据框,有score列,和ret, cap,等列
        Notes:
            ic用的是绝对值.
            对于第1期, 权重采用的的是第1期的ic
            默认的min_periods采用1, 也就是计算滑动均值和方差时, 对于小于滑动窗口的前几期, 采用的是实际长度,而不是填充NA
        """
        ic = get_ic().abs()
        rolling_ic = ic.rolling(score_window, min_periods=1).mean()
        rolling_std = ic.rolling(score_window, min_periods=1).std()
        weight = rolling_ic / rolling_std
        weight.loc[0, :] = rolling_ic.loc[0, :]
        rank = get_rank()
        score_ = (
            rank * weight).sum(axis=1).to_frame().rename(columns={0: 'score'})
        return score_.join(fac_ret_data)


    def ret_weighted():
        first_group_returns = []
        group_ascending_default = dict(
            list(zip(factor_names, [False] * len(factor_names))))
        if isinstance(group_ascending, dict):
            group_ascending_default.update(**group_ascending)
        for factor_name in factor_names:
            # perhaps only ['ret'] other than other_names
            data = fac_ret_data[[factor_name] + other_names]
            data = add_group(data, num_group=num_group, ascending=group_ascending_default[factor_name], method='first',
                             na_option='keep')
            # 取得每个因子第一个分组的收益率
            ret = data.groupby([data.index.get_level_values(0), data.group])['ret'].mean().unstack()[['Q01']].rename(
                columns={'Q01': factor_name})
        returns = pd.concat(first_group_returns, axis=1)
        returns = returns.rolling(12, min_periods=1).mean()
        weight = returns / returns.sum(axis=1)
        rank = get_rank()

        score_ = (
            rank * weight).sum(axis=1).to_frame().rename(columns={0: 'score'})

        return score_.join(fac_ret_data)


    valid_method = {'equal_weighted': equal_weighted,
                    'ic_weighted': ic_weighted,
                    'icir_weighted': icir_weighted,
                    'ret_weighted': ret_weighted,
                    'context_weighted': context_weighted}
    try:
        return valid_method[method]()
    except KeyError:
        print(('{} is not a valid method. valid methods are: {}'.format(
            method, list(valid_method.keys()))))
        raise


def multiple_factors_analysis(data, pipeline, params=None):
    """
    多因子分析
    Args:
        data (DataFrame): 一个multi_index 数据框, 有因子, 对应下期收益率, 市值列. multi_index-->level0=dt, level1=code
        pipeline(List): 前面N-1个为对数据进行处理, 最后一个元素为一个元组,元组的元素为xxx_analysis
        params (dict): key为pipeline里面的函数名称, value该函数的参数, 为一字典

    Returns:
        多因子分析结果
    Examples:
        from analysis import filter_out_st, filter_out_suspend, filter_out_recently_ipo
        from analysis import prepare_data, add_group, de_extreme
        from analysis import information_coefficient_analysis, return_analysis, code_analysis, turnover_analysis

        data = prepare_data(factor_name=["M004009", "M004023"], index_code='000300', benchmark_code='000300',
        start_date='2015-01-01', end_date='2016-01-01, freq='M')
        pipeline = [filter_out_st, filter_out_suspend, filter_out_recently_ipo,de_extreme, standardize, score, add_group,
        (information_coefficient_analysis, return_analysis, code_analysis, turnover_analysis)]
        params = {'de_extreme': {'num':1, 'method': 'mad'},
                  'standardize': dict(method='cap'),
        }
        result = multiple_factors_analysis(data, pipeline, params)
    """
    if os.path.exists('multi_data.hd5'):
        X=pd.read_hdf('multi_data.hd5')
    else:
        X = data.copy()
        for func in pipeline[:-1]:
            X = func(X, **(params.get(func.__name__, {})))
        X.to_hdf('multi_data.hd5','df')
    result_dict = {}
    for func in pipeline[-1]:
        result_dict[func.__name__] = func(X, **(params.get(func.__name__, {})))
    # factor_name = get_factor_name(data)
    factor_result = FactorData(name='multiple', **result_dict)
    return factor_result
