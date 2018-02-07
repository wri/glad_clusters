import os
import numpy as np
from skimage import io
import matplotlib.pyplot as plt


URL_TMPL='{}/{}/{}/{}.png'
SIZE=256
VALUE=1
VALUE_BAND=2
FIGSIZE=(4,4)
ROW_FIGSIZE=(18,3)
CLUSTER_MARKER='o'
CLUSTER_SIZE=20
CLUSTER_COLOR='r'
#
#  
#
class ClusterViewer(object):

    @staticmethod
    def show(im,i=None,j=None,ax=None):
        if (i and j):
            if ax:
                show=False
            else:
                show=True
                fig, ax = plt.subplots(1,1, figsize=FIGSIZE)
            ax.imshow(im)
            ax.scatter([j],[i],
                marker=CLUSTER_MARKER,
                c=CLUSTER_COLOR,
                s=CLUSTER_SIZE)
            if show:
                plt.show()
        else:
            io.imshow(im)


    def __init__(self,service,url_base=None):
        self.service=service
        self.url_base=url_base or os.environ.get('url')


    def tile(self,
            row_id=None,
            error=False,
            x=None,
            y=None,
            z=None,
            show=True,
            array=False):
        if row_id:
            if error: df=self.service.errors()
            else: df=self.service.dataframe()
            z,x,y=df[['z','x','y']].iloc[row_id]
        arr=io.imread(self._url(z,x,y))
        if show:
            ClusterViewer.show(arr)
        if array:
            return arr


    def input(self,row_id,info=True):
        row=self.service.cluster(row_id,as_view=False)
        count,area,z,x,y,i,j,dmin,dmax=self._cluster_info(row)
        alerts=self._to_image(row.input_data)
        if info:
            print("COUNT: {}".format(count))
            print("AREA: {}".format(area))
            print("POINT: {},{}".format(i,j))
            print("ZXY: {}/{}/{}".format(z,x,y))
            print("DATES: {}-{}".format(dmin,dmax))
        ClusterViewer.show(alerts,i,j)


    def cluster(self,row_id,info=True):
        row=self.service.cluster(row_id,as_view=False)
        count,area,z,x,y,i,j,dmin,dmax=self._cluster_info(row)
        alerts=self._to_image(row.alerts)
        if info:
            print("COUNT: {}".format(count))
            print("AREA: {}".format(area))
            print("POINT: {},{}".format(i,j))
            print("ZXY: {}/{}/{}".format(z,x,y))
            print("DATES: {}-{}".format(dmin,dmax))
        ClusterViewer.show(alerts,i,j)


    def clusters(self,start=None,end=None,row_ids=[]):
        if row_ids:
            rows=self.service.dataframe().iloc[row_ids]
        else:
            rows=self.service.dataframe()[start:end]
        fig, axs = plt.subplots(1,rows.shape[0], figsize=ROW_FIGSIZE)
        i=0
        for row_id,row in rows.iterrows():
            self._cluster_axis(axs[i],row)
            i+=1
        plt.show()


    def _cluster_info(self,row):
        return (
            row['count'],
            row.area,
            row.z,row.x,row.y,
            row.i,row.j,
            row.min_date,row.max_date)


    def _cluster_axis(self,ax,row):
        count,area,z,x,y,i,j,dmin,dmax=self._cluster_info(row)
        alerts=self._to_image(row.alerts)
        title='count:{}, area:{}, pt:{},{}'.format(count,area,i,j)
        subtitle='dates: {}-{}'.format(dmin,dmax)        
        ClusterViewer.show(alerts,i,j,ax)
        ax.scatter([j],[i],marker='o',c='r',s=20)
        ax.set_title(title)
        ax.set_xlabel(subtitle)



    def _to_image(self,data):
        data=data.astype(int)
        im=np.zeros((SIZE,SIZE))
        nb_bands=data.shape[1]
        if nb_bands==2:
            im[data[:,0],data[:,1]]=VALUE
        else:
            im[data[:,0],data[:,1]]=data[:,VALUE_BAND]
        return im


    def _url(self,z,x,y):
        return URL_TMPL.format(self.url_base,z,x,y)